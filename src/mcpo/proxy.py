import argparse
import asyncio
import json
import logging
import os
import re
import socket
import signal
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Dict, Any, Optional, AsyncGenerator
from urllib.parse import urlsplit

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from fastmcp.mcp_config import MCPConfig
from fastmcp.server.providers.proxy import ProxyClient, StatefulProxyClient
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from mcpo.services.logging import get_log_manager
from mcpo.services.logging_handlers import BufferedLogHandler
from mcpo.services.file_logging import (
    reattach_uvicorn_file_handlers,
    setup_file_logging,
)
from mcpo.services.state import get_state_manager
from mcpo.middleware.mcp_tool_filter import MCPToolFilterMiddleware
from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.utils.auth import APIKeyMiddleware
from mcpo.utils.config import (
    interpolate_env_placeholders_in_config,
    normalize_config_shape,
    vendor_site_port_from_env,
)
from mcpo.utils.oauth_self_hosted import KeyGatedOAuthProvider

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8001
DEFAULT_PATH = "/global"
PROXY_MAX_LOG_ENTRIES = 2000

logger = logging.getLogger(__name__)

_proxy_log_buffer: list[Dict[str, Any]] = []
_proxy_log_lock = threading.Lock()


def _find_port_owner_pid(port: int) -> Optional[int]:
    """Best-effort lookup of the PID currently listening on `port` (via netstat).

    Informational only for the pre-flight error message; returns None (not an
    exception) if it can't be determined cheaply.
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except Exception:
        return None

    pattern = re.compile(rf":{port}\s+\S+\s+LISTENING\s+(\d+)\s*$", re.MULTILINE)
    match = pattern.search(out)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


async def _enumerate_own_child_process_ids() -> set[int]:
    """Return every OS-level descendant PID owned by this proxy process."""
    my_pid = os.getpid()

    def _descendants(process_rows: list[tuple[int, int]]) -> set[int]:
        by_parent: Dict[int, list[int]] = {}
        for pid, parent_pid in process_rows:
            by_parent.setdefault(parent_pid, []).append(pid)
        descendants: set[int] = set()
        pending = [my_pid]
        while pending:
            parent_pid = pending.pop()
            for child_pid in by_parent.get(parent_pid, []):
                if child_pid not in descendants:
                    descendants.add(child_pid)
                    pending.append(child_pid)
        return descendants

    def _run_windows() -> set[int]:
        ps_script = (
            "Get-CimInstance Win32_Process | "
            "ForEach-Object { '{0},{1}' -f $_.ProcessId,$_.ParentProcessId }"
        )
        try:
            output = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout
        except Exception as exc:
            raise RuntimeError(
                "Failed to enumerate Windows child processes"
            ) from exc

        rows: list[tuple[int, int]] = []
        for line in output.splitlines():
            try:
                pid_text, parent_text = line.strip().split(",", 1)
                rows.append((int(pid_text), int(parent_text)))
            except (TypeError, ValueError):
                continue
        return _descendants(rows)

    def _run_posix() -> set[int]:
        try:
            output = subprocess.run(
                ["ps", "-eo", "pid=,ppid="],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout
        except Exception as exc:
            raise RuntimeError(
                "Failed to enumerate POSIX child processes"
            ) from exc

        rows: list[tuple[int, int]] = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2:
                continue
            try:
                rows.append((int(fields[0]), int(fields[1])))
            except ValueError:
                continue
        return _descendants(rows)

    runner = _run_windows if os.name == "nt" else _run_posix
    return await asyncio.to_thread(runner)


async def _kill_own_child_process_tree(
    *,
    preserve_pids: Optional[set[int]] = None,
) -> int:
    """Kill owned MCP descendants and verify every targeted PID disappeared."""
    child_pids = await _enumerate_own_child_process_ids()
    child_pids.difference_update(preserve_pids or set())

    def _kill_all() -> None:
        for pid in child_pids:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except Exception:
                logger.warning(
                    "HOT-RELOAD: failed to kill child PID %s",
                    pid,
                    exc_info=True,
                )

    if child_pids:
        await asyncio.to_thread(_kill_all)
        remaining = set(child_pids)
        for _attempt in range(5):
            remaining = (
                await _enumerate_own_child_process_ids()
            ).intersection(child_pids)
            if not remaining:
                break
            await asyncio.sleep(0.05)
        if remaining:
            remaining_text = ", ".join(str(pid) for pid in sorted(remaining))
            raise RuntimeError(
                f"Failed to terminate owned MCP child PID(s): {remaining_text}"
            )

    return len(child_pids)

def _preflight_check_port(host: str, port: int) -> None:
    """Test-bind host:port and immediately close, before building the FastMCP app or
    spawning any MCP child processes.

    uvicorn only binds its real listen socket after ASGI lifespan startup completes
    (which is what spawns/attaches the MCP child fleet). Without this check, starting
    into an already-occupied port spawns the entire fleet first and only then dies on
    uvicorn's own bind error, leaving that fleet orphaned with nothing serving it. This
    check runs before any of that work starts, so a collision spawns nothing and exits
    immediately.

    Plain bind + close, no SO_REUSEADDR/SO_EXCLUSIVEADDRUSE tricks, and the socket is
    not held open or handed to uvicorn: this probe must observe exactly what uvicorn's
    own bind will see, then get out of the way.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as exc:
        owner_pid = _find_port_owner_pid(port)
        owner_note = f" (owned by PID {owner_pid})" if owner_pid else " (owning PID not determined)"
        sys.stderr.write(
            "\n" + "=" * 70 + "\n"
            f"PORT {port} IS ALREADY IN USE{owner_note}\n"
            f"Another process is already listening on {host}:{port}. Refusing to\n"
            f"start -- no MCP servers were spawned. Stop the existing process first\n"
            f"(stop.bat, or: powershell tools\\clear_ports.ps1 -Ports {port}) and retry.\n"
            f"[{exc}]\n"
            + "=" * 70 + "\n"
        )
        sys.exit(1)
    finally:
        probe.close()


def _ensure_log_manager_capacity() -> None:
    """Initialize the shared log manager with an adequate buffer."""
    manager = get_log_manager(PROXY_MAX_LOG_ENTRIES)
    manager.max_entries = max(manager.max_entries, PROXY_MAX_LOG_ENTRIES)


def _build_proxy_log_router() -> APIRouter:
    router = APIRouter()

    @router.get("/_meta/logs")
    async def get_proxy_logs(
        category: Optional[str] = None,
        source: Optional[str] = None,
        cursor: Optional[int] = None,
        limit: int = 500,
    ):
        log_manager = get_log_manager(PROXY_MAX_LOG_ENTRIES)
        log_source = source or "mcp"
        effective_limit = max(1, min(limit, PROXY_MAX_LOG_ENTRIES))
        logs = log_manager.get_logs(
            category=category,
            source=log_source,
            after=cursor,
            limit=effective_limit,
        )
        latest_cursor = log_manager.get_latest_sequence()
        next_cursor = logs[-1]["sequence"] if logs else latest_cursor
        return {
            "ok": True,
            "logs": logs,
            "nextCursor": next_cursor,
            "latestCursor": latest_cursor,
            "limit": effective_limit,
        }

    @router.get("/_meta/logs/categorized")
    async def get_proxy_logs_categorized(source: Optional[str] = None):
        log_manager = get_log_manager(PROXY_MAX_LOG_ENTRIES)
        categorized = log_manager.get_logs_categorized(source=source or "mcp")
        cats: Dict[str, Dict[str, Any]] = {}
        for category, entries in categorized.items():
            cats[category] = {"count": len(entries), "logs": entries}
        return {"ok": True, "categories": cats}

    @router.post("/_meta/logs/clear/{category}")
    async def clear_proxy_logs(category: str):
        log_manager = get_log_manager(PROXY_MAX_LOG_ENTRIES)
        if category in ("all", "*"):
            log_manager.clear_logs(source="mcp")
        else:
            log_manager.clear_logs(category, source="mcp")
        return {"ok": True}

    @router.post("/_meta/logs/clear/all")
    async def clear_proxy_logs_all():
        log_manager = get_log_manager(PROXY_MAX_LOG_ENTRIES)
        log_manager.clear_logs(source="mcp")
        return {"ok": True}

    @router.get("/_meta/logs/sources")
    async def list_proxy_sources(request: Request):
        host = getattr(request.app.state, "bound_host", DEFAULT_HOST)
        port = getattr(request.app.state, "bound_port", DEFAULT_PORT)
        mounts = getattr(request.app.state, "proxy_mounts", [])
        sources: list[dict[str, Any]] = []
        for mount in mounts:
            mount_path = (mount.get("path") or "/")
            if mount_path != "/" and not mount_path.startswith("/"):
                mount_path = f"/{mount_path.lstrip('/')}"
            url_suffix = "" if mount_path in ("", "/") else mount_path
            label = mount.get("label") or mount.get("id") or (mount_path.lstrip("/") or "MCP Proxy")
            source_entry = {
                "id": mount.get("id") or mount_path.lstrip("/") or "root",
                "label": label,
                "host": host,
                "port": port,
                "url": f"http://{host}:{port}{url_suffix}",
                "available": True,
                "path": mount_path,
            }
            if "scope" in mount:
                source_entry["scope"] = mount["scope"]
            if "server" in mount:
                source_entry["server"] = mount["server"]
            sources.append(source_entry)
        if not sources:
            sources.append(
                {
                    "id": "mcp",
                    "label": "MCP Proxy",
                    "host": host,
                    "port": port,
                    "url": f"http://{host}:{port}",
                    "available": True,
                    "path": "/",
                }
            )
        return {"ok": True, "sources": sources}

    return router



def _create_fastmcp_lifespan(fastmcp_apps: list[tuple[FastAPI, str]]):
    """
    Create a lifespan context manager for FastMCP sub-app lifespans.
    
    Replaces the deprecated @app.on_event pattern with modern lifespan approach.
    """
    from contextlib import AsyncExitStack

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: initialize all FastMCP sub-app lifespans
        stack = AsyncExitStack()
        async with stack:
            for sub_app, label in fastmcp_apps:
                try:
                    await stack.enter_async_context(sub_app.router.lifespan_context(sub_app))
                    logger.debug("Started FastMCP lifespan for %s", label)
                except Exception:
                    logger.exception("Failed to start FastMCP lifespan for %s", label)
                    raise
            
            yield  # Application runs here
            
            # Shutdown handled automatically by AsyncExitStack.__aexit__
            logger.debug("FastMCP lifespans shutting down")
    
    return lifespan


# OpenHubUI MCP server removed - now using OpenAPI endpoints only
def _interpolate_env_placeholders(config_obj: dict) -> dict:
    """Expand supported ${VAR} placeholders in MCP server configuration."""
    return interpolate_env_placeholders_in_config(config_obj)



class _TokenGate:
    """Require a valid OAuth access token (issued by the self-hosted AS) on a sub-app.

    Used to protect the per-server MCP endpoints, which have no OAuth discovery of
    their own. Reuses the provider's token store, so tokens minted via /token work
    on every endpoint. This is the OAuth analog of the plain proxy's APIKeyMiddleware.
    """

    def __init__(self, app, provider):
        self.app = app
        self.provider = provider

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = None
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                val = v.decode("latin-1")
                if val.lower().startswith("bearer "):
                    token = val[7:]
                break
        if token and await self.provider.verify_token(token):
            await self.app(scope, receive, send)
            return
        body = b'{"detail":"Missing or invalid access token"}'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b"Bearer"),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


def _load_filtered_config(config_path: Path) -> tuple[MCPConfig, Dict[str, Any]]:
    """Load config and omit only servers structurally disabled in that config."""
    with config_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)
    raw_cfg = normalize_config_shape(raw_cfg)
    raw_cfg = _interpolate_env_placeholders(raw_cfg)

    servers_cfg = raw_cfg.get("mcpServers", {}) if isinstance(raw_cfg, dict) else {}
    filtered_servers: Dict[str, Any] = {}
    for server_name, server_cfg in servers_cfg.items():
        if isinstance(server_cfg, dict) and not server_cfg.get("enabled", True):
            logger.info("Skipping FastMCP server '%s' (disabled in config)", server_name)
            continue
        filtered_servers[server_name] = server_cfg

    if isinstance(raw_cfg, dict):
        raw_cfg = dict(raw_cfg)
        raw_cfg["mcpServers"] = filtered_servers
    if servers_cfg and not filtered_servers:
        logger.warning(
            "All MCP servers are disabled in config; proxy will expose metadata only"
        )

    return MCPConfig.from_dict(raw_cfg), filtered_servers


def _proxy_backend_mode() -> str | None:
    """Mirror the active front connection's protocol era onto its backend."""
    from fastmcp.server.dependencies import get_context
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS

    try:
        request_context = get_context().request_context
    except RuntimeError:
        return None
    if request_context is None:
        return None
    version = request_context.protocol_version
    if version in MODERN_PROTOCOL_VERSIONS:
        return version
    return "legacy"


_MODERN_ENVELOPE_META_KEYS = frozenset(
    {
        PROTOCOL_VERSION_META_KEY,
        CLIENT_INFO_META_KEY,
        CLIENT_CAPABILITIES_META_KEY,
    }
)


def _strip_modern_envelope_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Remove only 2026 envelope fields before a strict legacy backend call."""
    if not meta:
        return meta

    stripped = {
        key: value
        for key, value in meta.items()
        if key not in _MODERN_ENVELOPE_META_KEYS
    }
    return stripped or None


class _LegacyMetaBridgeMixin:
    """Keep modern envelope metadata off a negotiated legacy connection."""

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler=None,
        timeout=None,
        meta: dict[str, Any] | None = None,
    ):
        if self.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            meta = _strip_modern_envelope_meta(meta)

        return await super().call_tool_mcp(
            name,
            arguments,
            progress_handler=progress_handler,
            timeout=timeout,
            meta=meta,
        )


class _BridgedProxyClient(_LegacyMetaBridgeMixin, ProxyClient):
    pass


class _BridgedStatefulProxyClient(_LegacyMetaBridgeMixin, StatefulProxyClient):
    pass


class _EraAwareProxyClientFactory:
    """Bridge the front era while negotiating each vendor backend independently."""

    def __init__(self, cfg: MCPConfig, *, stateless: bool) -> None:
        self.cfg = cfg
        self.stateless = stateless
        self.clients: dict[str, Any] = {}

    def client_for_mode(self, front_mode: str):
        client_mode = front_mode if len(self.cfg.mcpServers) > 1 else "auto"
        client = self.clients.get(client_mode)
        if client is None:
            client_type = (
                _BridgedProxyClient if self.stateless else _BridgedStatefulProxyClient
            )
            client = client_type(self.cfg, roots=[], mode=client_mode)
            client._transport_options = replace(
                client._transport_options,
                backend_mode="auto",
            )
            self.clients[client_mode] = client
        return client

    def __call__(self):
        # A single-server config connects straight to the vendor backend, so it
        # can auto-negotiate there. A multi-server config first connects to
        # FastMCP's internal composite and must mirror the front era on that
        # outer leg; backend_mode="auto" independently negotiates every member.
        mode = "auto"
        if len(self.cfg.mcpServers) > 1:
            mode = _proxy_backend_mode() or "legacy"
        client = self.client_for_mode(mode)
        if self.stateless:
            return client.new()
        return client.new_stateful()


def _mcp_transport_security_kwargs(public_url: str | None = None) -> Dict[str, Any]:
    """Return strict Host/Origin guard settings for a FastMCP HTTP app."""
    settings: Dict[str, Any] = {
        "host_origin_protection": True,
        "allowed_hosts": ["host.docker.internal"],
    }
    if not public_url:
        return settings

    parsed = urlsplit(public_url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("--public-url must be an absolute HTTP(S) URL without credentials")
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    settings["allowed_hosts"] = [parsed.hostname]
    settings["allowed_origins"] = [origin]
    return settings


def _add_mcp_host_origin_guard(
    app: FastAPI,
    public_url: str | None = None,
) -> None:
    """Put FastMCP's strict Host/Origin guard outside authentication."""
    from fastmcp.server.http import HostOriginGuardMiddleware

    settings = _mcp_transport_security_kwargs(public_url)
    app.add_middleware(
        HostOriginGuardMiddleware,
        allowed_hosts=settings.get("allowed_hosts"),
        allowed_origins=settings.get("allowed_origins"),
        mode="strict",
    )


def _proxy_no_roots_forward(
    cfg: MCPConfig,
    auth=None,
    stateless: bool = False,
    server_name: Optional[str] = None,
):
    """Build a roots-suppressed, era-aware FastMCP proxy with BOS policy hooks."""
    from fastmcp.server.providers.proxy import FastMCPProxy

    kwargs: Dict[str, Any] = {
        "client_factory": _EraAwareProxyClientFactory(cfg, stateless=stateless),
        "provider_error_strategy": "raise",
    }
    if auth is not None:
        kwargs["auth"] = auth
    configured_server_names = tuple(cfg.mcpServers)
    policy_server_name = server_name
    if policy_server_name is None and len(configured_server_names) == 1:
        policy_server_name = configured_server_names[0]

    proxy = FastMCPProxy(**kwargs)
    # Code mode is outermost so its catalog sees the already-filtered tool list,
    # and rewritten execute_tool calls pass through the filter before dispatch.
    proxy.add_middleware(
        CodeModeMCPMiddleware(
            server_name=policy_server_name,
            server_names=configured_server_names,
        )
    )
    proxy.add_middleware(
        MCPToolFilterMiddleware(
            server_name=policy_server_name,
            server_names=configured_server_names,
        )
    )
    return proxy


def _build_oauth_app(
    *,
    cfg: MCPConfig,
    servers: Dict[str, Any],
    host: str,
    port: int,
    public_url: str,
    api_key: str,
    storage_path: Path,
    stateless_http: Optional[bool],
) -> FastAPI:
    """Build (without serving) the FastAPI app for OAuth-protected proxy mode.

    Aggregate endpoint /mcp + OAuth discovery routes at the root, plus a token-gated
    per-server endpoint at /{name}/ for each enabled server. Used both for the direct
    run and by the uvicorn reload factory.
    """
    if not api_key:
        raise ValueError("OAuth mode requires --api-key (the key entered on the consent page)")
    if not public_url:
        raise ValueError("OAuth mode requires --public-url (the public https URL clients reach, e.g. https://dev.ai.lighting)")

    provider = KeyGatedOAuthProvider(
        base_url=public_url,
        api_key=api_key,
        storage_path=storage_path,
    )
    proxy = _proxy_no_roots_forward(
        cfg,
        auth=provider,
        stateless=bool(stateless_http),
        server_name=None,
    )

    fastmcp_apps: list[tuple[FastAPI, str]] = []
    lifespan = _create_fastmcp_lifespan(fastmcp_apps)
    api_app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _add_mcp_host_origin_guard(api_app, public_url)

    mcp_app = proxy.http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=stateless_http,
        **_mcp_transport_security_kwargs(public_url),
    )
    setattr(mcp_app.state, "is_fastmcp_proxy", True)
    setattr(mcp_app.state, "proxy_scope", "global")
    fastmcp_apps.append((mcp_app, "oauth aggregate mount /"))

    # Per-server endpoints at /{name}, token-gated, mirroring the plain proxy so the
    # tunnel behaves identically. Mounted BEFORE the "/" catch-all so they match first.
    for server_name, server_cfg in (servers or {}).items():
        mount_segment = str(server_name).strip().strip("/")
        if not mount_segment:
            continue
        try:
            single_cfg = MCPConfig.from_dict({"mcpServers": {server_name: server_cfg}})
            single_proxy = _proxy_no_roots_forward(
                single_cfg,
                stateless=bool(stateless_http),
                server_name=server_name,
            )
        except Exception:
            logger.error("Failed to init OAuth per-server proxy for '%s'", server_name, exc_info=True)
            continue
        sub_app = single_proxy.http_app(
            path="/",
            transport="streamable-http",
            stateless_http=stateless_http,
            **_mcp_transport_security_kwargs(public_url),
        )
        setattr(sub_app.state, "is_fastmcp_proxy", True)
        setattr(sub_app.state, "proxy_scope", "server")
        setattr(sub_app.state, "proxy_server_name", server_name)
        fastmcp_apps.append((sub_app, f"oauth server {server_name} (/{mount_segment})"))
        wrapped = _TokenGate(sub_app, provider)
        api_app.mount(f"/{mount_segment}", wrapped)
        logger.info("Mounted OAuth per-server MCP for '%s' at /%s", server_name, mount_segment)

    api_app.mount("/", mcp_app)

    api_app.state.bound_host = host
    api_app.state.bound_port = port
    api_app.state.auth_enabled = True
    api_app.state.oauth_public_url = public_url

    logger.info("OAuth self-hosted AS enabled; public URL %s, MCP endpoint %s/mcp", public_url, public_url.rstrip("/"))
    return api_app


def oauth_app_factory() -> FastAPI:
    """Build the OAuth proxy app from environment variables.

    Import target for uvicorn's reloader. On each (re)start uvicorn calls this, so
    structural config changes are re-read and the mounts rebuilt from scratch.
    """
    logging.basicConfig(level=logging.INFO)
    # Reloader child process: attach the rotating file log here (the parent
    # supervisor skips it so only one process holds the file).
    setup_file_logging("proxy", int(os.environ.get("MCPO_OAUTH_PORT", "8351")))
    reattach_uvicorn_file_handlers()
    config_path = Path(os.environ["MCPO_OAUTH_CONFIG"]).expanduser()
    cfg, servers = _load_filtered_config(config_path)
    return _build_oauth_app(
        cfg=cfg,
        servers=servers,
        host=os.environ.get("MCPO_OAUTH_HOST", DEFAULT_HOST),
        port=int(os.environ.get("MCPO_OAUTH_PORT", "8351")),
        public_url=os.environ.get("MCPO_OAUTH_PUBLIC_URL", ""),
        api_key=os.environ.get("MCPO_OAUTH_API_KEY", ""),
        storage_path=config_path.parent / ".mcpo_oauth_state.json",
        stateless_http=True if os.environ.get("MCPO_OAUTH_STATELESS") == "1" else None,
    )


async def _run_oauth_proxy(
    *,
    cfg: MCPConfig,
    servers: Dict[str, Any],
    config_path: Path,
    host: str,
    port: int,
    public_url: str,
    api_key: str,
    storage_path: Path,
    stateless_http: Optional[bool],
    log_level: str,
    hot_reload: bool = False,
) -> None:
    """Serve the OAuth-protected proxy. With hot_reload, uvicorn watches structural
    config changes. Shared-state toggles are enforced at request time without a
    process restart. Issued tokens survive because they are persisted to disk.
    """
    if hot_reload:
        os.environ["MCPO_OAUTH_CONFIG"] = str(config_path)
        os.environ["MCPO_OAUTH_HOST"] = host
        os.environ["MCPO_OAUTH_PORT"] = str(port)
        os.environ["MCPO_OAUTH_PUBLIC_URL"] = public_url
        os.environ["MCPO_OAUTH_API_KEY"] = api_key
        os.environ["MCPO_OAUTH_STATELESS"] = "1" if stateless_http else "0"
        watch_dir = str(config_path.parent)
        config = uvicorn.Config(
            "mcpo.proxy:oauth_app_factory",
            factory=True,
            host=host,
            port=port,
            log_level=log_level,
            timeout_graceful_shutdown=0,
            lifespan="on",
            reload=True,
            reload_dirs=[watch_dir],
            reload_includes=[f"*{config_path.name}"],
            reload_excludes=[f"*{storage_path.name}", "*.tmp", "*.log"],
        )
        server = uvicorn.Server(config)
        logger.info("Hot-reload enabled; watching %s for %s", watch_dir, config_path.name)
        await server.serve()
        return

    api_app = _build_oauth_app(
        cfg=cfg,
        servers=servers,
        host=host,
        port=port,
        public_url=public_url,
        api_key=api_key,
        storage_path=storage_path,
        stateless_http=stateless_http,
    )
    config = uvicorn.Config(
        api_app,
        host=host,
        port=port,
        log_level=log_level,
        timeout_graceful_shutdown=0,
        lifespan="on",
    )
    # uvicorn.Config's dictConfig strips handlers from the non-propagating
    # uvicorn loggers; re-attach the file handler so access/error lines persist.
    reattach_uvicorn_file_handlers()
    server = uvicorn.Server(config)
    await server.serve()


def _record_plain_proxy_teardown_failure(
    api_app: FastAPI,
    *,
    label: str,
    lifespan: Any,
    error: BaseException,
) -> None:
    """Retain a failed lifespan handle and its error for control-plane diagnostics."""
    failures = dict(
        getattr(api_app.state, "fastmcp_proxy_teardown_failures", {}) or {}
    )
    failures[f"{label}#{id(lifespan)}"] = {
        "lifespan": lifespan,
        "error": f"{type(error).__name__}: {error}",
    }
    api_app.state.fastmcp_proxy_teardown_failures = failures


async def _teardown_plain_proxy_mounts(api_app: FastAPI) -> tuple[int, int, int]:
    """Unpublish and stop current plain proxy mounts, failing on incomplete cleanup."""
    removed = 0
    for route in list(api_app.router.routes):
        route_app = getattr(route, "app", None)
        if (
            route_app is not None
            and hasattr(route_app, "state")
            and getattr(route_app.state, "is_fastmcp_proxy", False)
        ):
            api_app.router.routes.remove(route)
            removed += 1
    api_app.state.proxy_mounts = []
    old_lifespans = dict(
        getattr(api_app.state, "fastmcp_proxy_lifespans", {}) or {}
    )
    api_app.state.fastmcp_proxy_lifespans = {}

    failed_labels: list[str] = []
    for mount_path, lifespan in reversed(list(old_lifespans.items())):
        try:
            await lifespan.__aexit__(None, None, None)
        except BaseException as exc:
            failed_labels.append(mount_path)
            _record_plain_proxy_teardown_failure(
                api_app,
                label=f"shutdown:{mount_path}",
                lifespan=lifespan,
                error=exc,
            )
            logger.error(
                "HOT-RELOAD: error shutting down FastMCP lifespan for %s",
                mount_path,
                exc_info=True,
            )

    killed = 0
    if old_lifespans:
        killed = await _kill_own_child_process_tree()
        logger.info(
            "HOT-RELOAD: killed %d old MCP backend process(es) during teardown",
            killed,
        )

    if failed_labels:
        raise RuntimeError(
            "Failed to close FastMCP lifespan(s): "
            + ", ".join(sorted(failed_labels))
        )
    return removed, len(old_lifespans), killed

async def _rebuild_plain_proxy_mounts(
    api_app: FastAPI,
    *,
    config_path: Path,
    path: str,
    stateless_http: Optional[bool],
) -> None:
    """Build a complete candidate generation before publishing or stopping the old one."""
    cfg, filtered_servers = _load_filtered_config(config_path)

    old_routes = [
        route
        for route in api_app.router.routes
        if bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
    ]
    old_lifespans = dict(
        getattr(api_app.state, "fastmcp_proxy_lifespans", {}) or {}
    )
    old_child_pids = await _enumerate_own_child_process_ids()

    new_lifespans: Dict[str, Any] = {}
    candidate_routes: list[Mount] = []
    proxy_mounts: list[dict[str, Any]] = []
    candidate_global_mount: Optional[str] = None

    global_mount_path = path or "/global"
    if not global_mount_path.startswith("/"):
        global_mount_path = f"/{global_mount_path}"
    global_mount_path = global_mount_path.rstrip("/") or "/"

    try:
        if filtered_servers:
            proxy = _proxy_no_roots_forward(
                cfg,
                stateless=bool(stateless_http),
                server_name=None,
            )
            global_app = proxy.http_app(
                path="/",
                transport="streamable-http",
                stateless_http=stateless_http,
                **_mcp_transport_security_kwargs(),
            )
            setattr(global_app.state, "is_fastmcp_proxy", True)
            setattr(global_app.state, "proxy_scope", "global")

            global_cm = global_app.router.lifespan_context(global_app)
            await global_cm.__aenter__()
            new_lifespans[global_mount_path] = global_cm
            candidate_routes.append(Mount(global_mount_path, app=global_app))
            candidate_global_mount = global_mount_path
            proxy_mounts.append(
                {
                    "id": "global" if global_mount_path != "/" else "root",
                    "label": "All MCPs",
                    "path": global_mount_path,
                    "scope": "global",
                }
            )

            if global_mount_path != "/mcp":
                candidate_routes.append(Mount("/mcp", app=global_app))
                proxy_mounts.append(
                    {
                        "id": "mcp",
                        "label": "All MCPs (/mcp)",
                        "path": "/mcp",
                        "scope": "global",
                    }
                )

        for server_name, server_cfg in filtered_servers.items():
            mount_segment = str(server_name).strip().strip("/")
            if not mount_segment:
                raise ValueError("MCP server names must not be blank")

            root_mount_path = (
                f"/{mount_segment}".replace("//", "/").rstrip("/") or "/"
            )
            if any(
                existing.get("path") == root_mount_path
                for existing in proxy_mounts
            ):
                raise ValueError(
                    f"Mount path {root_mount_path} is already in use"
                )

            single_cfg = MCPConfig.from_dict(
                {"mcpServers": {server_name: server_cfg}}
            )
            single_proxy = _proxy_no_roots_forward(
                single_cfg,
                stateless=bool(stateless_http),
                server_name=server_name,
            )
            sub_app = single_proxy.http_app(
                path="/",
                transport="streamable-http",
                stateless_http=stateless_http,
                **_mcp_transport_security_kwargs(),
            )
            setattr(sub_app.state, "is_fastmcp_proxy", True)
            setattr(sub_app.state, "proxy_scope", "server")
            setattr(sub_app.state, "proxy_server_name", server_name)

            sub_cm = sub_app.router.lifespan_context(sub_app)
            await sub_cm.__aenter__()
            new_lifespans[root_mount_path] = sub_cm
            candidate_routes.append(Mount(root_mount_path, app=sub_app))
            label = (
                server_cfg.get("name")
                if isinstance(server_cfg, dict)
                else None
            ) or server_name
            proxy_mounts.append(
                {
                    "id": mount_segment,
                    "label": label,
                    "path": root_mount_path,
                    "scope": "server",
                    "server": server_name,
                }
            )

        all_child_pids = await _enumerate_own_child_process_ids()
        candidate_child_pids = all_child_pids - old_child_pids
    except BaseException:
        for mount_path, cm in reversed(list(new_lifespans.items())):
            try:
                await cm.__aexit__(None, None, None)
            except BaseException as cleanup_error:
                _record_plain_proxy_teardown_failure(
                    api_app,
                    label=f"candidate:{mount_path}",
                    lifespan=cm,
                    error=cleanup_error,
                )
                logger.error(
                    "HOT-RELOAD: candidate cleanup failed for %s",
                    mount_path,
                    exc_info=True,
                )
        await _kill_own_child_process_tree(preserve_pids=old_child_pids)
        raise

    old_route_ids = {id(route) for route in old_routes}
    current_routes = list(api_app.router.routes)
    insertion_index = min(
        (
            index
            for index, route in enumerate(current_routes)
            if id(route) in old_route_ids
        ),
        default=len(current_routes),
    )
    retained_routes = [
        route for route in current_routes if id(route) not in old_route_ids
    ]
    insertion_index = min(insertion_index, len(retained_routes))
    api_app.router.routes[:] = (
        retained_routes[:insertion_index]
        + candidate_routes
        + retained_routes[insertion_index:]
    )
    api_app.state.proxy_mounts = proxy_mounts
    api_app.state.fastmcp_proxy_lifespans = new_lifespans
    api_app.state.proxy_global_mount = candidate_global_mount
    api_app.state.proxy_stateless = stateless_http

    for mount_path, cm in reversed(list(old_lifespans.items())):
        try:
            await cm.__aexit__(None, None, None)
        except BaseException as retirement_error:
            _record_plain_proxy_teardown_failure(
                api_app,
                label=f"retired:{mount_path}",
                lifespan=cm,
                error=retirement_error,
            )
            logger.error(
                "HOT-RELOAD: error shutting down previous FastMCP lifespan for %s",
                mount_path,
                exc_info=True,
            )
    if old_lifespans or old_child_pids:
        killed = await _kill_own_child_process_tree(
            preserve_pids=candidate_child_pids
        )
        logger.info(
            "HOT-RELOAD: killed %d old MCP backend process(es) after swap",
            killed,
        )

    logger.info(
        "HOT-RELOAD: atomically published %d server(s) across %d route(s); "
        "retired %d old route(s) and %d old lifespan(s)",
        len(filtered_servers),
        len(candidate_routes),
        len(old_routes),
        len(old_lifespans),
    )

async def _plain_proxy_mount_manager(
    api_app: FastAPI,
    *,
    config_path: Path,
    path: str,
    stateless_http: Optional[bool],
    rebuild_queue: "asyncio.Queue",
    startup_future: "asyncio.Future[None]",
) -> None:
    """Own every FastMCP sub-app lifespan from one long-lived asyncio task.

    FastMCP streamable-http lifespans use AnyIO cancel scopes, which must be
    entered and exited by the same task. Watchers only enqueue rebuild requests.
    """
    try:
        try:
            await _rebuild_plain_proxy_mounts(
                api_app,
                config_path=config_path,
                path=path,
                stateless_http=stateless_http,
            )
        except Exception as startup_error:
            if not startup_future.done():
                startup_future.set_exception(startup_error)
            return

        if not startup_future.done():
            startup_future.set_result(None)

        while True:
            await rebuild_queue.get()
            while not rebuild_queue.empty():
                rebuild_queue.get_nowait()

            logger.info(
                "HOT-RELOAD: change detected, rebuilding FastMCP proxy mounts..."
            )
            try:
                await _rebuild_plain_proxy_mounts(
                    api_app,
                    config_path=config_path,
                    path=path,
                    stateless_http=stateless_http,
                )
            except Exception:
                logger.error(
                    "HOT-RELOAD: rebuild failed; routes remain at the last "
                    "completed publication",
                    exc_info=True,
                )
    finally:
        if not startup_future.done():
            startup_future.cancel()
        await _teardown_plain_proxy_mounts(api_app)

def _start_plain_hot_reload_watchers(
    rebuild_queue: "asyncio.Queue",
    *,
    config_path: Path,
) -> list:
    """Watch structural config; each change enqueues a rebuild request for the
    single mount-manager task (see _plain_proxy_mount_manager) to pick up.

    Reuses ConfigWatcher (utils/config_watcher.py, watchdog-based) -- the same
    mechanism main.py already uses to hot-reload the port-8000 admin app's config,
    so this follows repo convention rather than introducing a second file-watching
    approach. The callback only pushes to a queue (no lifespan/cancel-scope work),
    so it's safe to run from whatever task watchdog's event delivery lands on.
    """
    from mcpo.utils.config_watcher import ConfigWatcher

    async def _on_change(_new_payload: Dict[str, Any]) -> None:
        rebuild_queue.put_nowait(None)

    watchers: list = []

    config_watcher = ConfigWatcher(str(config_path), _on_change)
    config_watcher.start()
    watchers.append(config_watcher)
    logger.info("HOT-RELOAD: watching %s for changes", config_path)

    return watchers


async def run_proxy(
    *,
    config_path: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    path: str = DEFAULT_PATH,
    log_level: str = "info",
    stateless_http: Optional[bool] = None,
    api_key: Optional[str] = None,
    oauth: bool = False,
    public_url: Optional[str] = None,
    hot_reload: bool = False,
) -> None:
    """Start the FastMCP Streamable HTTP proxy with UI log capture enabled."""
    # Pre-flight: fail fast on a port collision before building anything or spawning
    # any MCP child process (covers both the oauth and plain branches below, and both
    # with and without --hot-reload -- this check runs before either is decided).
    _preflight_check_port(host, port)

    logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))

    _ensure_log_manager_capacity()
    root_logger = logging.getLogger()
    buffered_handler = BufferedLogHandler(
        _proxy_log_buffer,
        _proxy_log_lock,
        default_source="mcp",
        max_entries=PROXY_MAX_LOG_ENTRIES,
    )
    if not any(isinstance(h, BufferedLogHandler) for h in root_logger.handlers):
        root_logger.addHandler(buffered_handler)
    # Capture uvicorn-specific loggers that bypass the root logger so UI logs include proxy requests.
    for logger_name in ("uvicorn.access", "uvicorn.error"):
        logger_obj = logging.getLogger(logger_name)
        if not any(isinstance(h, BufferedLogHandler) for h in logger_obj.handlers):
            logger_obj.addHandler(buffered_handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Persistent rotating file log (survives restarts, unlike the UI buffer).
    # Skipped for the oauth hot-reload branch: uvicorn's reloader runs the app
    # in a child process, and two processes holding the same rotating file
    # breaks rollover on Windows. The child sets it up in oauth_app_factory.
    if not (oauth and hot_reload):
        setup_file_logging("proxy", port)

    cfg, filtered_servers = _load_filtered_config(config_path)

    if oauth:
        await _run_oauth_proxy(
            cfg=cfg,
            servers=filtered_servers,
            config_path=config_path,
            host=host,
            port=port,
            public_url=public_url or "",
            api_key=api_key or "",
            storage_path=config_path.parent / ".mcpo_oauth_state.json",
            stateless_http=stateless_http,
            log_level=log_level,
            hot_reload=hot_reload,
        )
        return

    api_app = FastAPI()
    # Allow cross-origin requests for browser-based clients (e.g., OpenWebUI)
    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if api_key:
        api_app.add_middleware(APIKeyMiddleware, api_key=api_key)
    _add_mcp_host_origin_guard(api_app)
    api_app.state.bound_host = host
    api_app.state.bound_port = port
    api_app.state.auth_enabled = bool(api_key)
    api_app.state.fastmcp_proxy_lifespans = {}

    # Determine OpenAPI admin UI host:port from env or default
    _openapi_host = os.environ.get("MCPO_OPENAPI_HOST", "127.0.0.1")
    _openapi_port = os.environ.get("MCPO_OPENAPI_PORT", "8000")
    _openapi_base = f"http://{_openapi_host}:{_openapi_port}"

    # Friendly redirects so opening the proxy port in a browser isn't blank
    @api_app.get("/", response_class=HTMLResponse)
    async def _proxy_root_landing():
        try:
            # Redirect to the Admin UI if it's running on the common default port
            target = f"{_openapi_base}/ui/"
            return RedirectResponse(url=target, status_code=307)
        except Exception:
            # As a last resort, show a tiny hint page
            return HTMLResponse(
                content=(
                    "<html><body style='font-family:system-ui, sans-serif; padding:16px;'>"
                    "<h3>MCP Proxy</h3>"
                    "<p>This port serves the MCP Streamable HTTP proxy. The UI is available at "
                    f"<a href='{_openapi_base}/ui/'>{_openapi_base}/ui/</a>."
                    "</p>"
                    "</body></html>"
                ),
                status_code=200,
                media_type="text/html",
            )

    @api_app.get("/ui")
    async def _proxy_ui_redirect():
        return RedirectResponse(url=f"{_openapi_base}/ui/", status_code=307)

    hot_reload_watchers: list = []
    mount_manager_task: Optional[asyncio.Task] = None

    if hot_reload:
        # Every lifespan enter/exit for the life of the process runs inside the one
        # mount-manager task (see its docstring for why: anyio's cancel scopes are
        # task-affine, and this file previously entered a mount's lifespan in one
        # task and exited it in another, which crashed the whole process on reload).
        rebuild_queue: asyncio.Queue = asyncio.Queue()
        startup_future: asyncio.Future[None] = (
            asyncio.get_running_loop().create_future()
        )
        mount_manager_task = asyncio.create_task(
            _plain_proxy_mount_manager(
                api_app,
                config_path=config_path,
                path=path,
                stateless_http=stateless_http,
                rebuild_queue=rebuild_queue,
                startup_future=startup_future,
            )
        )
        try:
            await startup_future
        except BaseException:
            try:
                await mount_manager_task
            except BaseException:
                pass
            raise
        hot_reload_watchers = _start_plain_hot_reload_watchers(
            rebuild_queue,
            config_path=config_path,
        )
        logger.info(
            "HOT-RELOAD: enabled for structural config; toggle state is request-time"
        )
    else:
        # No hot-reload: build once, inline, in this task. Nothing will ever exit
        # these lifespans before process exit, so there's no cross-task risk.
        await _rebuild_plain_proxy_mounts(api_app, config_path=config_path, path=path, stateless_http=stateless_http)

    api_app.include_router(_build_proxy_log_router())

    config = uvicorn.Config(
        api_app,
        host=host,
        port=port,
        log_level=log_level,
        timeout_graceful_shutdown=0,
        lifespan="on",
    )
    # uvicorn.Config's dictConfig strips handlers from the non-propagating
    # uvicorn loggers; re-attach the file handler so access/error lines persist.
    reattach_uvicorn_file_handlers()
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        for watcher in hot_reload_watchers:
            watcher.stop()
        if mount_manager_task is not None:
            mount_manager_task.cancel()
            try:
                await mount_manager_task
            except asyncio.CancelledError:
                pass
        else:
            await _teardown_plain_proxy_mounts(api_app)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Start FastMCP Streamable HTTP proxy (8001) from mcpo.json")
    p.add_argument("--config", "-c", type=str, default="mcpo.json", help="Path to MCP config (mcpo.json)")
    p.add_argument("--host", "-H", type=str, default=DEFAULT_HOST, help="Bind host")
    p.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help="Bind port")
    p.add_argument("--path", type=str, default=DEFAULT_PATH, help="Mount path for the aggregate Streamable HTTP endpoint")
    p.add_argument("--log-level", "-l", type=str, default="info", help="Log level (debug, info, warning, error)")
    p.add_argument("--stateless-http", action="store_true", help="Force stateless HTTP mode")
    p.add_argument("--api-key", "-k", type=str, help="API key for Bearer or Basic authentication; defaults to MCPO_API_KEY")
    p.add_argument("--oauth", action="store_true", help="Serve a self-hosted OAuth 2.1 authorization server so OAuth-only clients (e.g. ChatGPT) can connect. Requires --api-key and --public-url.")
    p.add_argument("--public-url", type=str, help="Public https URL clients reach this server at (e.g. https://dev.ai.lighting). Required with --oauth.")
    p.add_argument("--hot-reload", action="store_true", help="Watch mcpo.json and rebuild endpoints on structural config changes. Shared-state toggles are enforced at request time without rebuilding MCP runtimes.")
    p.add_argument("--env", "-e", action="append", help="Extra env KEY=VALUE to set before start")
    p.add_argument("--env-path", type=str, help=".env file to load before start")
    return p.parse_args()


def apply_env(env_items: Optional[list[str]], env_path: Optional[str]) -> None:
    if env_path and Path(env_path).exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except Exception:
            pass
    if env_items:
        for item in env_items:
            if "=" in item:
                k, v = item.split("=", 1)
                os.environ[k] = v


def resolve_proxy_api_key(cli_api_key: Optional[str]) -> Optional[str]:
    return cli_api_key or os.environ.get("MCPO_API_KEY")


def main() -> None:
    args = parse_args()
    apply_env(args.env, args.env_path)
    vendor_site_port_from_env()
    api_key = resolve_proxy_api_key(args.api_key)

    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    asyncio.run(
        run_proxy(
            config_path=config_path,
            host=args.host,
            port=args.port,
            path=args.path,
            log_level=args.log_level,
            stateless_http=args.stateless_http or None,
            api_key=api_key,
            oauth=args.oauth,
            public_url=args.public_url,
            hot_reload=args.hot_reload,
        )
    )


if __name__ == "__main__":
    main()


