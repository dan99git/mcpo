import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.routing import Mount

# Router for all /_meta endpoints
router = APIRouter()

# Import services for cleaner architecture
from mcpo.services.state import StateSaveError, get_state_manager
from mcpo.services.logging import get_log_manager
from mcpo.services.runner import get_runner_service
from mcpo.services.metrics import get_metrics_aggregator

# Health snapshot helpers (owned by routers.health)
from mcpo.api.routers.health import _health_state, _update_health_snapshot

# Main-module helpers that still live in mcpo.main.
# Late-bound through the module object so unit tests that patch
# mcpo.main.<helper> (spawn/teardown/reload/mount/...) take effect here.
import mcpo.main as main_module
from mcpo.main import (
    unmount_servers,
    validate_server_config,
    error_envelope,
    MCP_VERSION,
    MAX_LOG_ENTRIES,
    ConfigRollbackError,
    _atomic_write_config,
    _find_server_mounts,
    _get_server_runtimes,
    _read_config_file_snapshot,
    _reload_lock,
    _runtime_is_alive,
    _server_mount_path,
    _server_name_from_mount,
    validate_config_data,
)
from mcpo.utils.main import normalize_server_type
from mcpo.utils.config import (
    normalize_config_shape,
    replace_mcp_servers_preserving_shape,
)

logger = logging.getLogger(__name__)

PROXY_REQUEST_TIMEOUT = 2.5


def _state_manager(request: Request):
    """Prefer the app-scoped StateManager (what tests patch) and fall back
    to the process-wide singleton."""
    sm = getattr(request.app.state, "state_manager", None)
    if sm is not None:
        return sm
    return get_state_manager()


async def _get_proxy_servers(request: Request) -> list[dict[str, Any]]:
    """Fetch server list from MCP proxy and format it."""
    proxy_servers = []
    proxy_data = await _proxy_meta_request(request, "/_meta/logs/sources")
    if not proxy_data or not proxy_data.get("ok"):
        return proxy_servers

    state_manager = _state_manager(request)
    sources = proxy_data.get("sources", [])
    for source in sources:
        # We only care about per-server mounts, not the global/aggregate ones
        if source.get("scope") != "server":
            continue

        server_name = source.get("server") or source.get("id")
        if not server_name:
            continue

        is_enabled = state_manager.is_server_enabled(server_name)
        proxy_servers.append({
            "name": server_name,
            "connected": False,
            "type": "internal" if server_name == "openhubui" else "proxy",
            "basePath": (source.get("path", f"/{server_name}/") or "/").rstrip("/") + "/",
            "enabled": is_enabled,
        })
    return proxy_servers


async def _proxy_meta_request(
    request: Request,
    path: str,
    *,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Forward a log-related request to the MCP proxy if configured."""
    base_url = getattr(request.app.state, "mcp_proxy_url", None)
    if not base_url:
        return None

    url = f"{base_url.rstrip('/')}{path}"
    # Forward the API key so the fetch authenticates against the MCP proxy's
    # /_meta endpoints (the proxy requires it whenever MCPO_API_KEY is set);
    # without this the fetch 401s and the UI shows no MCP logs.
    _proxy_key = getattr(request.app.state, "api_key", "") or os.environ.get("MCPO_API_KEY", "")
    _proxy_headers = {"Authorization": f"Bearer {_proxy_key}"} if _proxy_key else {}
    try:
        timeout = httpx.Timeout(PROXY_REQUEST_TIMEOUT, connect=1.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "POST":
                response = await client.post(url, params=params, headers=_proxy_headers)
            else:
                response = await client.get(url, params=params, headers=_proxy_headers)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug(f"Proxy meta request failed for {url}: {exc}")
    return None


@router.get("/servers")
async def list_servers(request: Request):
    main_app = request.app
    _update_health_snapshot(main_app)
    state_manager = _state_manager(request)
    # Seed from the config so disabled/unmounted servers still appear.
    config_data = getattr(main_app.state, "config_data", {}) or {}
    config_servers = config_data.get("mcpServers", {}) if isinstance(config_data, dict) else {}
    servers_by_name = {}
    for name, cfg in config_servers.items():
        enabled = bool(state_manager.is_server_enabled(name)) and not (
            isinstance(cfg, dict) and not cfg.get("enabled", True)
        )
        servers_by_name[name] = {
            "name": name,
            "connected": False,
            "type": normalize_server_type((cfg or {}).get("type")) if isinstance(cfg, dict) else "unknown",
            "basePath": f"{_server_mount_path(main_app, name).rstrip('/')}/",
            "enabled": enabled,
        }
    # Overlay live mount state.
    for route in main_app.router.routes:
        if not (isinstance(route, Mount) and isinstance(route.app, FastAPI)):
            continue
        sub = route.app
        config_key = _server_name_from_mount(route)
        # Include OpenHubUI admin tools, but skip other FastMCP proxy apps
        if getattr(sub.state, "is_fastmcp_proxy", False) and config_key != "openhubui":
            continue
        if sub.title == "MCPO Management Server":
            servers_by_name[config_key] = {
                "name": config_key, "connected": True, "type": "internal",
                "basePath": route.path.rstrip('/') + '/', "enabled": True,
            }
            continue
        if config_key not in config_servers:
            if config_key == "openhubui":
                servers_by_name[config_key] = {
                    "name": config_key,
                    "connected": bool(getattr(sub.state, "is_connected", False)),
                    "type": getattr(sub.state, "server_type", "internal"),
                    "basePath": route.path.rstrip('/') + '/',
                    "enabled": state_manager.is_server_enabled(config_key),
                }
            continue
        entry = servers_by_name[config_key]
        entry["connected"] = bool(getattr(sub.state, "is_connected", False))
        entry["type"] = getattr(sub.state, "server_type", entry["type"])
        entry["basePath"] = route.path.rstrip('/') + '/'

    # Fetch and merge servers known only to the MCP proxy
    try:
        proxy_servers = await _get_proxy_servers(request)
        for proxy_server in proxy_servers:
            if proxy_server["name"] not in servers_by_name:
                servers_by_name[proxy_server["name"]] = proxy_server
    except Exception as e:
        logger.warning(f"Could not retrieve servers from MCP proxy: {e}")

    # Sort servers alphabetically
    servers = sorted(servers_by_name.values(), key=lambda x: x["name"])

    return {"ok": True, "servers": servers}


@router.get("/servers/{server_name}/tools")
async def list_server_tools(request: Request, server_name: str):
    main_app = request.app
    state_manager = main_app.state.state_manager
    # Find mounted server
    for route in _find_server_mounts(main_app, server_name):
        if isinstance(route.app, FastAPI):
            sub = route.app
            tools = []
            for r in sub.router.routes:
                if hasattr(r, 'methods') and 'POST' in getattr(r, 'methods', []) and getattr(r, 'path', '/').startswith('/'):
                    p = r.path
                    if p == '/docs' or p.startswith('/openapi'):
                        continue
                    if p.count('/') == 1:  # '/tool'
                        tname = p.lstrip('/')
                        # Special handling for internal MCPO management server
                        if sub.title == "MCPO Management Server":
                            is_enabled = True  # Internal tools are always enabled
                        else:
                            is_enabled = state_manager.is_tool_enabled(server_name, tname)
                        
                        tools.append({
                            "name": tname,
                            "enabled": is_enabled
                        })
            return {"ok": True, "server": server_name, "tools": sorted(tools, key=lambda x: x['name'])}
    return JSONResponse(status_code=404, content=error_envelope("Server not found", code="not_found"))


@router.get("/config")
async def config_info(request: Request):
    path = getattr(request.app.state, 'config_path', None)
    return {"ok": True, "configPath": path}


@router.get("/config/content")
async def get_config_content(request: Request):
    path = getattr(request.app.state, 'config_path', None)
    if not path:
        return JSONResponse(status_code=400, content=error_envelope("No config file", code="no_config"))
    try:
        with open(path, 'r') as f:
            content = f.read()
        return {"ok": True, "content": content, "path": path}
    except FileNotFoundError:
        return JSONResponse(status_code=404, content=error_envelope("Config file not found", code="not_found"))
    except Exception as e:
        return JSONResponse(status_code=500, content=error_envelope("Failed to read config", data=str(e), code="io_error"))


@router.post("/config/save")
async def save_config_content(request: Request, payload: Dict[str, Any]):
    """Save config file contents with validation."""
    main_app = request.app
    if getattr(main_app.state, 'read_only_mode', False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    config_path = getattr(main_app.state, 'config_path', None)
    if not config_path:
        return JSONResponse(status_code=400, content=error_envelope("No config file configured", code="no_config"))
    
    content = payload.get("content")
    if not content or not isinstance(content, str):
        return JSONResponse(status_code=422, content=error_envelope("Missing or invalid content", code="invalid"))
    
    try:
        # Validate the complete configuration before touching the file.
        config_data = validate_config_data(json.loads(content))
        previous_config_data = deepcopy(
            getattr(main_app.state, 'config_data', {"mcpServers": {}})
        )
        previous_file_bytes = _read_config_file_snapshot(config_path)
        
        # Backup existing config
        backup_path = f"{config_path}.backup"
        if os.path.exists(config_path):
            import shutil
            shutil.copy2(config_path, backup_path)
        
        # Save new config atomically, preserving the old file on write failure.
        _atomic_write_config(config_path, content)
        
        # Reload configuration and the MCP protocol mounts.
        try:
            await main_module._reload_config_with_rollback(
                main_app,
                config_data,
                previous_config_data,
                config_path,
                previous_file_bytes,
            )
        except ConfigRollbackError as e:
            return JSONResponse(
                status_code=500,
                content=error_envelope(
                    "Configuration update and rollback failed",
                    data={"update": str(e.update_error), "rollback": str(e.rollback_error)},
                    code="rollback_failed",
                ),
            )
        except Exception as e:
            return JSONResponse(status_code=500, content=error_envelope("Reload failed", data=str(e), code="reload_failed"))
        main_app.state.aggregate_openapi_dirty = True
        
        return {"ok": True, "message": "Configuration saved and reloaded", "backup": backup_path}
        
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=422, content=error_envelope("Invalid JSON format", data=str(e), code="invalid"))
    except ValueError as e:
        return JSONResponse(status_code=422, content=error_envelope(str(e), code="invalid"))
    except Exception as e:
        return JSONResponse(status_code=500, content=error_envelope("Failed to save config", data=str(e), code="io_error"))


@router.get("/config/mcpServers")
async def get_mcp_servers_content(request: Request):
    """Return only the mcpServers section as a JSON string.

    Response shape:
    { ok: true, content: "{...}" }
    """
    path = getattr(request.app.state, 'config_path', None)
    if not path:
        return JSONResponse(status_code=400, content=error_envelope("No config file", code="no_config"))
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
        mcp_servers = normalize_config_shape(cfg).get("mcpServers", {})
        content = json.dumps(mcp_servers, indent=2)
        return {"ok": True, "content": content, "path": path}
    except FileNotFoundError:
        return JSONResponse(status_code=404, content=error_envelope("Config file not found", code="not_found"))
    except Exception as e:
        return JSONResponse(status_code=500, content=error_envelope("Failed to read mcpServers", data=str(e), code="io_error"))


@router.post("/config/mcpServers/save")
async def save_mcp_servers_content(request: Request, payload: Dict[str, Any]):
    """Update only the authoritative mcpServers section and reload."""
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=error_envelope("Read-only mode enabled", code="read_only"),
        )
    config_path = getattr(main_app.state, "config_path", None)
    if not config_path:
        return JSONResponse(
            status_code=400,
            content=error_envelope("No config file configured", code="no_config"),
        )

    has_content = "content" in payload
    has_data = "data" in payload
    if not has_content and not has_data:
        return JSONResponse(
            status_code=422,
            content=error_envelope("Missing content or data", code="invalid"),
        )

    try:
        new_mcp = (
            json.loads(payload["content"])
            if has_content
            else payload["data"]
        )
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "Invalid JSON",
                data=str(exc),
                code="invalid_json",
            ),
        )

    if not isinstance(new_mcp, dict):
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "mcpServers must be an object",
                code="invalid_type",
            ),
        )

    previous_file_bytes = _read_config_file_snapshot(config_path)
    if previous_file_bytes is None:
        return JSONResponse(
            status_code=404,
            content=error_envelope("Config file not found", code="not_found"),
        )
    try:
        raw_config = json.loads(previous_file_bytes.decode("utf-8-sig"))
        raw_candidate = replace_mcp_servers_preserving_shape(
            raw_config,
            new_mcp,
        )
        new_config = validate_config_data(raw_candidate)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(str(exc), code="invalid"),
        )

    previous_config_data = deepcopy(
        getattr(main_app.state, "config_data", {"mcpServers": {}})
    )
    try:
        _atomic_write_config(
            config_path,
            json.dumps(raw_candidate, indent=2),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Failed to save mcpServers",
                data=str(exc),
                code="io_error",
            ),
        )

    try:
        await main_module._reload_config_with_rollback(
            main_app,
            new_config,
            previous_config_data,
            config_path,
            previous_file_bytes,
        )
    except ConfigRollbackError as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Configuration update and rollback failed",
                data={
                    "update": str(exc.update_error),
                    "rollback": str(exc.rollback_error),
                },
                code="rollback_failed",
            ),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Reload failed",
                data=str(exc),
                code="reload_failed",
            ),
        )

    main_app.state.aggregate_openapi_dirty = True
    return {"ok": True, "saved": True, "reloaded": True}


@router.get("/logs")
async def get_logs(
    request: Request,
    source: Optional[str] = None,
    category: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = 500,
):
    """Get recent log entries for UI display."""
    log_source = (source or "openapi").lower()
    effective_limit = max(1, min(limit, MAX_LOG_ENTRIES))

    if log_source == "mcp":
        proxy_data = await _proxy_meta_request(
            request,
            "/_meta/logs",
            params={
                key: value
                for key, value in {
                    "category": category,
                    "cursor": cursor,
                    "limit": effective_limit,
                    "source": "mcp",
                }.items()
                if value is not None
            }
        )
        if proxy_data:
            return proxy_data

    # Exclude httpx logs from openapi source to avoid showing proxy fetch logs
    exclude_logger = "httpx" if log_source == "openapi" else None

    log_manager = get_log_manager(MAX_LOG_ENTRIES)
    entries = log_manager.get_logs(
        category=category,
        source=log_source,
        after=cursor,
        limit=effective_limit,
        exclude_logger=exclude_logger,
    )
    latest_cursor = log_manager.get_latest_sequence()
    next_cursor = entries[-1]["sequence"] if entries else latest_cursor
    return {
        "ok": True,
        "logs": entries,
        "nextCursor": next_cursor,
        "latestCursor": latest_cursor,
        "limit": effective_limit,
    }


@router.get("/logs/categorized")
async def get_logs_categorized(request: Request, source: Optional[str] = None):
    log_manager = get_log_manager()

    if source == "mcp":
        proxy_data = await _proxy_meta_request(request, "/_meta/logs/categorized")
        if proxy_data:
            return proxy_data

    categorized_logs = log_manager.get_logs_categorized(source=source)

    cats: Dict[str, Dict[str, Any]] = {}
    for category, logs in categorized_logs.items():
        cats[category] = {"count": len(logs), "logs": logs}
    return {"ok": True, "categories": cats}


@router.get("/logs/sources")
async def get_log_sources(request: Request):
    """Describe available log sources for the UI."""
    log_manager = get_log_manager()
    tracked_sources = set(log_manager.get_sources())

    bound_host = getattr(request.app.state, "bound_host", None)
    bound_port = getattr(request.app.state, "bound_port", None)

    sources: list[Dict[str, Any]] = [
        {
            "id": "openapi",
            "label": "OpenAPI Server",
            "host": bound_host,
            "port": bound_port,
            "url": f"http://{bound_host}:{bound_port}" if bound_host and bound_port else None,
            "available": True,
        }
    ]

    proxy_url = getattr(request.app.state, "mcp_proxy_url", None)
    if proxy_url:
        parsed = urlparse(proxy_url)
        proxy_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sources.append(
            {
                "id": "mcp",
                "label": "MCP Proxy",
                "host": parsed.hostname,
                "port": proxy_port,
                "url": proxy_url,
                "available": True,
            }
        )
    else:
        sources.append(
            {
                "id": "mcp",
                "label": "MCP Proxy",
                "available": False,
            }
        )

    for entry in sources:
        if entry["id"] in tracked_sources:
            entry["available"] = True

    return {"ok": True, "sources": sources}


@router.post("/logs/clear/{category}")
async def clear_logs_category(category: str, request: Request, source: Optional[str] = None):
    if source == "mcp":
        proxy_data = await _proxy_meta_request(
            request, f"/_meta/logs/clear/{category}", method="POST"
        )
        if proxy_data:
            return proxy_data

    log_manager = get_log_manager()
    if category in ("all", "*"):
        log_manager.clear_logs(source=source)
    else:
        log_manager.clear_logs(category, source=source)
    return {"ok": True}


@router.post("/logs/clear/all")
async def clear_logs_all(request: Request, source: Optional[str] = None):
    if source == "mcp":
        proxy_data = await _proxy_meta_request(request, "/_meta/logs/clear/all", method="POST")
        if proxy_data:
            return proxy_data

    log_manager = get_log_manager()
    log_manager.clear_logs(source=source)
    return {"ok": True}


@router.post("/reload")
async def reload_config(request: Request):
    """Force a config reload (only valid when running from a config file).

    This compares config, mounts/unmounts servers, and initializes newly added ones
    so that their tools become available immediately.
    """
    main_app = request.app
    if getattr(main_app.state, 'read_only_mode', False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    path = getattr(main_app.state, 'config_path', None)
    if not path:
        return JSONResponse(status_code=400, content=error_envelope("No config-driven servers active", code="no_config"))
    try:
        new_config = main_module.load_config(path)
        await main_module._reload_runtime_surfaces_with_rollback(main_app, new_config)
        main_app.state.aggregate_openapi_dirty = True
        return {"ok": True, "generation": _health_state["generation"], "lastReload": _health_state["last_reload"]}
    except Exception as e:  # pragma: no cover - defensive
        return JSONResponse(status_code=500, content=error_envelope("Reload failed", data=str(e), code="reload_failed"))


@router.post("/reinit/{server_name}")
async def reinit_server(request: Request, server_name: str):
    """Tear down and reinitialize one mounted server session."""
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=error_envelope(
                "Read-only mode enabled",
                code="read_only",
            ),
        )

    async with _reload_lock:
        for route in _find_server_mounts(main_app, server_name):
            if not isinstance(route.app, FastAPI):
                continue
            sub_app = route.app
            try:
                teardown_result = await main_module.teardown_server_runtime(
                    main_app,
                    server_name,
                )
                if not teardown_result.get("ok", True):
                    error = (
                        teardown_result.get("error")
                        or "unknown teardown error"
                    )
                    logger.error(
                        "Reinit teardown for '%s' failed: %s",
                        server_name,
                        error,
                    )
                    return JSONResponse(
                        status_code=502,
                        content=error_envelope(
                            "Server teardown failed",
                            data=error,
                            code="teardown_failed",
                        ),
                    )
                sub_app.state.is_connected = False
                await main_module.initialize_sub_app(sub_app)
                main_app.state.aggregate_openapi_dirty = True
                return {
                    "ok": True,
                    "server": server_name,
                    "connected": bool(
                        getattr(sub_app.state, "is_connected", False)
                    ),
                }
            except Exception as exc:  # pragma: no cover
                return JSONResponse(
                    status_code=500,
                    content=error_envelope(
                        "Reinit failed",
                        data=str(exc),
                        code="reinit_failed",
                    ),
                )
    return JSONResponse(
        status_code=404,
        content=error_envelope(
            "Server not found",
            code="not_found",
        ),
    )


@router.post("/install-dependencies")
async def install_dependencies():
    if not os.path.exists("requirements.txt"):
        return JSONResponse(status_code=400, content=error_envelope("requirements.txt not found", code="no_requirements"))

    async def _run_install():
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                "requirements.txt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            async for line in proc.stdout:
                text = line.decode(errors="replace").rstrip()
                record = logging.LogRecord(
                    name="pip",
                    level=logging.INFO,
                    pathname=__file__,
                    lineno=0,
                    msg=text,
                    args=(),
                    exc_info=None,
                )
                logging.getLogger().handle(record)
            await proc.wait()
            logging.info("Dependency installation finished with code %s", proc.returncode)
        except Exception as e:
            logging.error(f"Dependency installation failed: {e}")

    # fire-and-forget background task
    asyncio.create_task(_run_install())
    return {"ok": True, "started": True}


@router.post("/servers/{server_name}/enable")
async def enable_server(request: Request, server_name: str):
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    async with _reload_lock:
        state_manager = main_app.state.state_manager
        config_data = getattr(main_app.state, "config_data", {}) or {}
        server_cfg = (
            config_data.get("mcpServers", {}) if isinstance(config_data, dict) else {}
        ).get(server_name)
        if not isinstance(server_cfg, dict):
            return JSONResponse(status_code=404, content=error_envelope("Server not found", code="not_found"))
        if not server_cfg.get("enabled", True):
            return JSONResponse(status_code=409, content=error_envelope("Server disabled in config", code="config_disabled"))

        previous_enabled = state_manager.is_server_enabled(server_name)
        try:
            if not previous_enabled:
                state_manager.set_server_enabled(server_name, True)
        except StateSaveError as exc:
            return JSONResponse(
                status_code=500,
                content=error_envelope(
                    "Failed to persist server state",
                    data=str(exc),
                    code="state_save_failed",
                ),
            )

        current_runtime = _get_server_runtimes(main_app).get(server_name)
        current_mounts = _find_server_mounts(main_app, server_name)
        if (
            _runtime_is_alive(current_runtime)
            and current_runtime.connected
            and current_mounts
        ):
            try:
                await main_module._sync_initialized_fastmcp_proxy(main_app)
            except Exception as remount_exc:
                try:
                    if not previous_enabled:
                        state_manager.set_server_enabled(server_name, False)
                    await main_module._sync_initialized_fastmcp_proxy(main_app)
                except Exception as rollback_exc:
                    return JSONResponse(
                        status_code=500,
                        content=error_envelope(
                            "Native MCP rollback failed",
                            data={
                                "remount": str(remount_exc),
                                "rollback": str(rollback_exc),
                            },
                            code="state_rollback_failed",
                        ),
                    )
                return JSONResponse(
                    status_code=502,
                    content=error_envelope(
                        "Native MCP remount failed",
                        data=str(remount_exc),
                        code="native_remount_failed",
                    ),
                )
            return {"ok": True, "server": server_name, "enabled": True, "connected": True}

        backup_routes = list(main_app.router.routes)
        had_connected_runtime = bool(
            _runtime_is_alive(current_runtime) and current_runtime.connected
        )
        if _runtime_is_alive(current_runtime):
            teardown_result = await main_module.teardown_server_runtime(main_app, server_name)
            if not teardown_result.get("ok", True):
                if not previous_enabled:
                    try:
                        state_manager.set_server_enabled(server_name, False)
                        await main_module._sync_initialized_fastmcp_proxy(main_app)
                    except Exception as rollback_exc:
                        return JSONResponse(
                            status_code=500,
                            content=error_envelope(
                                "Server state rollback failed",
                                data={"activation": teardown_result.get("error"), "rollback": str(rollback_exc)},
                                code="state_rollback_failed",
                            ),
                        )
                return JSONResponse(status_code=502, content=error_envelope(
                    "Server activation failed", data=teardown_result.get("error"), code="activation_failed"))

        removed_routes = main_module._remove_server_mounts(main_app, server_name)
        sub_app = main_module._create_configured_server_app(main_app, server_name)
        if sub_app is None:
            main_app.router.routes = backup_routes
            try:
                if not previous_enabled:
                    state_manager.set_server_enabled(server_name, False)
                await main_module._sync_initialized_fastmcp_proxy(main_app)
            except Exception as rollback_exc:
                return JSONResponse(
                    status_code=500,
                    content=error_envelope(
                        "Server state rollback failed",
                        data=str(rollback_exc),
                        code="state_rollback_failed",
                    ),
                )
            return JSONResponse(status_code=404, content=error_envelope("Server not found", code="not_found"))

        try:
            runtime = await main_module.spawn_server_runtime(
                main_app,
                server_name,
                sub_app,
                api_dependency=getattr(main_app.state, "api_dependency", None),
            )
            if not runtime.connected:
                raise RuntimeError(runtime.last_error or "Server did not connect")
        except Exception as exc:
            await main_module.teardown_server_runtime(main_app, server_name)
            try:
                if previous_enabled and had_connected_runtime:
                    await main_module._restore_server_after_failed_disable(
                        main_app,
                        server_name,
                        backup_routes,
                        removed_routes,
                    )
                else:
                    main_app.router.routes = backup_routes
                if not previous_enabled:
                    state_manager.set_server_enabled(server_name, False)
                await main_module._sync_initialized_fastmcp_proxy(main_app)
            except Exception as rollback_exc:
                return JSONResponse(
                    status_code=500,
                    content=error_envelope(
                        "Server activation rollback failed",
                        data={"activation": str(exc), "rollback": str(rollback_exc)},
                        code="state_rollback_failed",
                    ),
                )
            logger.error("Failed to enable server '%s': %s", server_name, exc, exc_info=True)
            return JSONResponse(status_code=502, content=error_envelope(
                "Server activation failed", data=str(exc), code="activation_failed"))

        main_app.mount(_server_mount_path(main_app, server_name), sub_app)
        try:
            await main_module._sync_initialized_fastmcp_proxy(main_app)
        except Exception as remount_exc:
            await main_module.teardown_server_runtime(main_app, server_name)
            main_module._remove_server_mounts(main_app, server_name)
            try:
                if previous_enabled and had_connected_runtime:
                    await main_module._restore_server_after_failed_disable(
                        main_app,
                        server_name,
                        backup_routes,
                        removed_routes,
                    )
                else:
                    main_app.router.routes = backup_routes
                if not previous_enabled:
                    state_manager.set_server_enabled(server_name, False)
                await main_module._sync_initialized_fastmcp_proxy(main_app)
            except Exception as rollback_exc:
                return JSONResponse(
                    status_code=500,
                    content=error_envelope(
                        "Native MCP rollback failed",
                        data={
                            "remount": str(remount_exc),
                            "rollback": str(rollback_exc),
                        },
                        code="state_rollback_failed",
                    ),
                )
            return JSONResponse(
                status_code=502,
                content=error_envelope(
                    "Native MCP remount failed",
                    data=str(remount_exc),
                    code="native_remount_failed",
                ),
            )

        main_app.state.aggregate_openapi_dirty = True
        return {"ok": True, "server": server_name, "enabled": True, "connected": True}


@router.post("/servers/{server_name}/disable")
async def disable_server(request: Request, server_name: str):
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    async with _reload_lock:
        state_manager = main_app.state.state_manager
        previous_enabled = state_manager.is_server_enabled(server_name)
        try:
            if previous_enabled:
                state_manager.set_server_enabled(server_name, False)
        except StateSaveError as exc:
            return JSONResponse(
                status_code=500,
                content=error_envelope(
                    "Failed to persist server state",
                    data=str(exc),
                    code="state_save_failed",
                ),
            )

        backup_routes = list(main_app.router.routes)
        removed_routes = main_module._remove_server_mounts(main_app, server_name)
        try:
            teardown_result = await main_module.teardown_server_runtime(main_app, server_name)
        except Exception as exc:
            teardown_result = {"ok": False, "error": str(exc)}

        if not teardown_result.get("ok", True):
            if previous_enabled:
                try:
                    await main_module._restore_server_after_failed_disable(
                        main_app,
                        server_name,
                        backup_routes,
                        removed_routes,
                    )
                    state_manager.set_server_enabled(server_name, True)
                    await main_module._sync_initialized_fastmcp_proxy(main_app)
                except Exception as rollback_exc:
                    await main_module.teardown_server_runtime(main_app, server_name)
                    main_module._remove_server_mounts(main_app, server_name)
                    return JSONResponse(
                        status_code=500,
                        content=error_envelope(
                            "Server disable rollback failed",
                            data={
                                "teardown": teardown_result.get("error"),
                                "rollback": str(rollback_exc),
                            },
                            code="state_rollback_failed",
                        ),
                    )
            return JSONResponse(status_code=502, content=error_envelope(
                "Server shutdown failed", data=teardown_result.get("error"), code="teardown_failed"))

        try:
            await main_module._sync_initialized_fastmcp_proxy(main_app)
        except Exception as remount_exc:
            try:
                if previous_enabled:
                    await main_module._restore_server_after_failed_disable(
                        main_app,
                        server_name,
                        backup_routes,
                        removed_routes,
                    )
                    state_manager.set_server_enabled(server_name, True)
                else:
                    main_app.router.routes = backup_routes
                await main_module._sync_initialized_fastmcp_proxy(main_app)
            except Exception as rollback_exc:
                state_cleanup_error = None
                try:
                    state_manager.set_server_enabled(server_name, False)
                except StateSaveError as state_exc:
                    state_cleanup_error = str(state_exc)
                cleanup_teardown = await main_module.teardown_server_runtime(
                    main_app,
                    server_name,
                )
                if cleanup_teardown.get("ok", True):
                    _get_server_runtimes(main_app).pop(server_name, None)
                main_module._remove_server_mounts(main_app, server_name)
                return JSONResponse(
                    status_code=500,
                    content=error_envelope(
                        "Native MCP rollback failed",
                        data={
                            "remount": str(remount_exc),
                            "rollback": str(rollback_exc),
                            "failClosedState": state_cleanup_error,
                        },
                        code="state_rollback_failed",
                    ),
                )
            return JSONResponse(
                status_code=502,
                content=error_envelope(
                    "Native MCP remount failed",
                    data=str(remount_exc),
                    code="native_remount_failed",
                ),
            )

        main_app.state.aggregate_openapi_dirty = True
        return {"ok": True, "server": server_name, "enabled": False, "teardown": teardown_result}


@router.post("/servers/{server_name}/tools/{tool_name:path}/enable")
async def enable_tool(server_name: str, tool_name: str, request: Request):
    """Enable a specific tool."""
    try:
        if getattr(request.app.state, "read_only_mode", False):
            return JSONResponse(status_code=403, content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}})
        state_manager = _state_manager(request)
        state_manager.set_tool_enabled(server_name, tool_name, True)
        
        # Invalidate aggregate OpenAPI cache
        request.app.state.aggregate_openapi_dirty = True
        
        logger.info(f"Tool '{tool_name}' on server '{server_name}' enabled")
        return JSONResponse(content={"ok": True, "server": server_name, "tool": tool_name, "enabled": True})
    except StateSaveError:
        # Propagate to the app-level handler, which returns the
        # state_save_failed envelope the UI (and tests) rely on.
        raise
    except Exception as e:
        logger.error(f"Error enabling tool {tool_name}: {e}")
        return JSONResponse(
            status_code=500,
            content=error_envelope(f"Failed to enable tool: {str(e)}")
        )


@router.post("/servers/{server_name}/tools/{tool_name:path}/disable")
async def disable_tool(server_name: str, tool_name: str, request: Request):
    """Disable a specific tool."""
    try:
        if getattr(request.app.state, "read_only_mode", False):
            return JSONResponse(status_code=403, content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}})
        state_manager = _state_manager(request)
        state_manager.set_tool_enabled(server_name, tool_name, False)
        
        # Invalidate aggregate OpenAPI cache
        request.app.state.aggregate_openapi_dirty = True
        
        logger.info(f"Tool '{tool_name}' on server '{server_name}' disabled")
        return JSONResponse(content={"ok": True, "server": server_name, "tool": tool_name, "enabled": False})
    except StateSaveError:
        # Propagate to the app-level handler, which returns the
        # state_save_failed envelope the UI (and tests) rely on.
        raise
    except Exception as e:
        logger.error(f"Error disabling tool {tool_name}: {e}")
        return JSONResponse(
            status_code=500,
            content=error_envelope(f"Failed to disable tool: {str(e)}")
        )


@router.post("/servers")
async def add_server(request: Request):
    """Add a new server to config (only in config-driven mode) and reload."""
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=error_envelope("Read-only mode enabled", code="read_only"),
        )
    cfg_path = getattr(main_app.state, "config_path", None)
    if not cfg_path:
        return JSONResponse(
            status_code=400,
            content=error_envelope(
                "Not running with a config file",
                code="no_config_mode",
            ),
        )

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content=error_envelope("Invalid JSON payload", code="invalid"),
        )

    name = payload.get("name")
    if not name or not isinstance(name, str):
        return JSONResponse(
            status_code=422,
            content=error_envelope("Missing name", code="invalid"),
        )
    name = name.strip()
    if not name:
        return JSONResponse(
            status_code=422,
            content=error_envelope("Missing name", code="invalid"),
        )

    server_entry: Dict[str, Any] = {}
    command_str = payload.get("command")
    if command_str:
        if not isinstance(command_str, str):
            return JSONResponse(
                status_code=422,
                content=error_envelope("command must be string", code="invalid"),
            )
        parts = command_str.strip().split()
        if not parts:
            return JSONResponse(
                status_code=422,
                content=error_envelope("Empty command", code="invalid"),
            )
        server_entry["command"] = parts[0]
        if len(parts) > 1:
            server_entry["args"] = parts[1:]

    url = payload.get("url")
    stype = payload.get("type")
    if url:
        server_entry["url"] = url
        if stype:
            server_entry["type"] = stype
    env = payload.get("env")
    if env and isinstance(env, dict):
        server_entry["env"] = env

    try:
        validate_server_config(name, server_entry)
    except Exception as exc:
        return JSONResponse(
            status_code=422,
            content=error_envelope(str(exc), code="invalid"),
        )

    previous_file_bytes = _read_config_file_snapshot(cfg_path)
    if previous_file_bytes is None:
        return JSONResponse(
            status_code=404,
            content=error_envelope("Config file not found", code="not_found"),
        )
    try:
        raw_config = json.loads(previous_file_bytes.decode("utf-8-sig"))
        validate_config_data(raw_config)
        normalized_raw = normalize_config_shape(raw_config)
        raw_servers = deepcopy(normalized_raw["mcpServers"])
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Current config is invalid",
                data=str(exc),
                code="config_invalid",
            ),
        )

    if name in raw_servers:
        return JSONResponse(
            status_code=409,
            content=error_envelope("Server already exists", code="exists"),
        )

    raw_servers[name] = server_entry
    raw_candidate = replace_mcp_servers_preserving_shape(
        raw_config,
        raw_servers,
    )
    config_data = validate_config_data(raw_candidate)
    previous_config_data = deepcopy(
        getattr(main_app.state, "config_data", {"mcpServers": {}})
    )
    try:
        _atomic_write_config(
            cfg_path,
            json.dumps(raw_candidate, indent=2),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Failed to write config",
                data=str(exc),
                code="io_error",
            ),
        )

    try:
        await main_module._reload_config_with_rollback(
            main_app,
            config_data,
            previous_config_data,
            cfg_path,
            previous_file_bytes,
        )
    except ConfigRollbackError as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Configuration update and rollback failed",
                data={
                    "update": str(exc.update_error),
                    "rollback": str(exc.rollback_error),
                },
                code="rollback_failed",
            ),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Reload failed",
                data=str(exc),
                code="reload_failed",
            ),
        )

    main_app.state.aggregate_openapi_dirty = True
    return {"ok": True, "server": name}


@router.delete("/servers/{server_name}")
async def remove_server(request: Request, server_name: str):
    main_app = request.app
    if getattr(main_app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=error_envelope("Read-only mode enabled", code="read_only"),
        )
    cfg_path = getattr(main_app.state, "config_path", None)
    if not cfg_path:
        return JSONResponse(
            status_code=400,
            content=error_envelope(
                "Not running with a config file",
                code="no_config_mode",
            ),
        )

    previous_file_bytes = _read_config_file_snapshot(cfg_path)
    if previous_file_bytes is None:
        return JSONResponse(
            status_code=404,
            content=error_envelope("Config file not found", code="not_found"),
        )
    try:
        raw_config = json.loads(previous_file_bytes.decode("utf-8-sig"))
        validate_config_data(raw_config)
        normalized_raw = normalize_config_shape(raw_config)
        raw_servers = deepcopy(normalized_raw["mcpServers"])
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Current config is invalid",
                data=str(exc),
                code="config_invalid",
            ),
        )

    if server_name not in raw_servers:
        return JSONResponse(
            status_code=404,
            content=error_envelope("Server not found", code="not_found"),
        )

    del raw_servers[server_name]
    raw_candidate = replace_mcp_servers_preserving_shape(
        raw_config,
        raw_servers,
    )
    config_data = validate_config_data(raw_candidate)
    previous_config_data = deepcopy(
        getattr(main_app.state, "config_data", {"mcpServers": {}})
    )
    try:
        _atomic_write_config(
            cfg_path,
            json.dumps(raw_candidate, indent=2),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Failed to write config",
                data=str(exc),
                code="io_error",
            ),
        )

    try:
        await main_module._reload_config_with_rollback(
            main_app,
            config_data,
            previous_config_data,
            cfg_path,
            previous_file_bytes,
        )
    except ConfigRollbackError as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Configuration update and rollback failed",
                data={
                    "update": str(exc.update_error),
                    "rollback": str(exc.rollback_error),
                },
                code="rollback_failed",
            ),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Reload failed",
                data=str(exc),
                code="reload_failed",
            ),
        )

    main_app.state.aggregate_openapi_dirty = True
    return {"ok": True, "removed": server_name}


@router.get("/status")
async def get_status(request: Request):
    """Get overall server status."""
    try:
        main_app = request.app
        servers_count = 0
        connected_count = 0
        
        for route in main_app.router.routes:
            if isinstance(route, Mount) and hasattr(route.app, 'title'):
                servers_count += 1
                if getattr(route.app.state, 'is_connected', False):
                    connected_count += 1
        
        return JSONResponse(content={
            "status": "running",
            "servers": {
                "total": servers_count,
                "connected": connected_count,
                "disconnected": servers_count - connected_count
            }
        })
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return JSONResponse(
            status_code=500,
            content=error_envelope(f"Failed to get status: {str(e)}")
        )


@router.get("/stats")
async def get_stats(request: Request):
    """Basic server stats for UI (uptime, version)."""
    try:
        main_app = request.app
        # Uptime since process start via lifespan timestamp
        start_ts = getattr(main_app.state, "start_time", None)
        if start_ts is None:
            # Initialize on first call if not set
            import time
            start_ts = time.time()
            setattr(main_app.state, "start_time", start_ts)
        import time
        uptime_s = time.time() - start_ts
        version = getattr(main_app, "version", None) or MCP_VERSION
        return JSONResponse(content={"ok": True, "uptimeSeconds": uptime_s, "version": version})
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return JSONResponse(status_code=500, content=error_envelope("Failed to get stats", data=str(e)))

@router.get("/metrics")
async def get_metrics(request: Request):
    """Operational metrics snapshot: server/tool counts plus per-tool runner stats."""
    try:
        state_manager = _state_manager(request)
        all_states = state_manager.get_all_states()

        servers_total = len(all_states)
        servers_enabled = sum(1 for state in all_states.values() if state.get('enabled', True))

        tools_total = 0
        tools_enabled = 0
        for state in all_states.values():
            server_tools = state.get('tools', {})
            tools_total += len(server_tools)
            tools_enabled += sum(1 for enabled in server_tools.values() if enabled)

        runner = get_runner_service()
        runner_per_tool = runner.get_metrics()

        per_tool_metrics: Dict[str, Dict[str, Any]] = {}
        for tname, stats in runner_per_tool.items():
            if not isinstance(stats, dict):
                continue
            per_tool_metrics[tname] = {
                'calls': stats.get('calls', 0) or 0,
                'errors': stats.get('errors', 0) or 0,
                'avgLatencyMs': stats.get('avgLatencyMs', 0.0) or 0.0,
                'maxLatencyMs': stats.get('maxLatencyMs', 0.0) or 0.0,
            }

        agg = get_metrics_aggregator().build_metrics(per_tool_metrics)
        return {
            "ok": True,
            "metrics": {
                "servers": {"total": servers_total, "enabled": servers_enabled},
                "tools": {"total": tools_total, "enabled": tools_enabled},
                "calls": {"total": agg.get("calls", 0)},
                "errors": agg.get("errors", {}),
                "perTool": agg.get("perTool", {}),
            },
        }
    except Exception as e:
        logger.error(f"Error getting metrics: {e}")
        return JSONResponse(status_code=500, content=error_envelope("Failed to get metrics", data=str(e), code="metrics_error"))

@router.get("/requirements/content")
async def get_requirements_content():
    """Get requirements.txt content."""
    try:
        if os.path.exists("requirements.txt"):
            with open("requirements.txt", 'r') as f:
                content = f.read()
            return {"ok": True, "content": content}
        else:
            return {"ok": True, "content": "# Python packages for MCP servers\n"}
    except Exception as e:
        return JSONResponse(status_code=500, content=error_envelope("Failed to read requirements", data=str(e), code="io_error"))


@router.post("/requirements/save")
async def save_requirements_content(payload: Dict[str, Any], request: Request):
    """Save requirements.txt content, install packages via pip, and reload servers."""
    main_app = request.app
    if getattr(main_app.state, 'read_only_mode', False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    content = payload.get("content")
    if content is None:
        return JSONResponse(status_code=422, content=error_envelope("Missing content", code="invalid"))

    try:
        with open("requirements.txt", 'w') as f:
            f.write(content)
        logger.info("Requirements.txt updated; installing packages...")

        packages = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                logger.error("pip install failed: %s", result.stderr)
                return JSONResponse(
                    status_code=500,
                    content=error_envelope("Dependency installation failed", data=result.stderr, code="pip_failed"),
                )
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=408,
                content=error_envelope("Dependency installation timed out", code="pip_timeout"),
            )

        # Reload servers if running with a config file
        config_path = getattr(main_app.state, 'config_path', None)
        if config_path and os.path.exists(config_path):
            try:
                new_config = main_module.load_config(config_path)
                await main_module.reload_config_handler(main_app, new_config)
                main_app.state.aggregate_openapi_dirty = True
            except Exception as e:  # pragma: no cover
                logger.warning("Reload after requirements install failed: %s", e)

        return {"ok": True, "message": "Requirements installed and servers reloaded", "packages": packages}
    except Exception as e:
        return JSONResponse(status_code=500, content=error_envelope("Failed to save requirements", data=str(e), code="io_error"))


@router.get("/aggregate_openapi")
async def aggregate_openapi(request: Request, force_refresh: bool = False):
    """Unified OpenAPI 3.1 spec combining all enabled MCP servers."""
    main_app = request.app
    state_manager = main_app.state.state_manager

    cache_key = "aggregate_openapi_cache"
    if not force_refresh and hasattr(main_app.state, cache_key):
        cache = getattr(main_app.state, cache_key)
        if cache and not getattr(main_app.state, "aggregate_openapi_dirty", False):
            return JSONResponse(content=cache["schema"])

    base_spec: Dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": "MCPO Aggregated API", "version": "1.0.0", "description": "Unified API combining all enabled MCP servers"},
        "paths": {},
        "components": {"schemas": {}, "securitySchemes": {}, "responses": {}, "parameters": {}},
        "tags": [],
    }
    if not state_manager.is_rest_tools_enabled():
        cache_data = {
            "schema": base_spec,
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        setattr(main_app.state, cache_key, cache_data)
        setattr(main_app.state, "aggregate_openapi_dirty", False)
        return JSONResponse(content=base_spec)

    server_tags: set = set()

    for route in main_app.router.routes:
        if not isinstance(route, Mount) or not isinstance(route.app, FastAPI):
            continue
        sub_app = route.app
        server_name = _server_name_from_mount(route)
        if getattr(sub_app.state, "is_fastmcp_proxy", False):
            continue
        if not state_manager.is_server_enabled(server_name):
            continue
        if not getattr(sub_app.state, "is_connected", False):
            if sub_app.title != "MCPO Management Server":
                continue
        try:
            server_spec = sub_app.openapi()
            if not server_spec or "paths" not in server_spec:
                continue
            mount_path = route.path.rstrip("/")
            server_title = server_spec.get("info", {}).get("title", server_name)
            server_tags.add(server_title)

            server_components = server_spec.get("components", {})
            if not isinstance(server_components, dict):
                server_components = {}

            component_suffix = "".join(
                char if char.isalnum() or char in ".-_" else "_"
                for char in server_name
            ).strip("_") or "server"
            component_mapping: Dict[tuple[str, str], str] = {}

            # Reserve every target name before copying content so local refs can
            # be rewritten even when they point to a later component.
            for comp_type, components in server_components.items():
                if not isinstance(components, dict):
                    continue
                target_components = base_spec["components"].setdefault(comp_type, {})
                reserved_names = set(target_components)
                for comp_name in components:
                    target_name = comp_name
                    if target_name in reserved_names:
                        base_name = f"{comp_name}_{component_suffix}"
                        target_name = base_name
                        suffix = 2
                        while target_name in reserved_names:
                            target_name = f"{base_name}_{suffix}"
                            suffix += 1
                    component_mapping[(comp_type, comp_name)] = target_name
                    reserved_names.add(target_name)

            def _rewrite_server_refs(value: Any) -> Any:
                if isinstance(value, dict):
                    rewritten = {
                        key: _rewrite_server_refs(child)
                        for key, child in value.items()
                    }
                    ref = value.get("$ref")
                    if isinstance(ref, str) and ref.startswith("#/components/"):
                        parts = ref.split("/")
                        if len(parts) >= 4:
                            target_name = component_mapping.get((parts[2], parts[3]))
                            if target_name:
                                parts[3] = target_name
                                rewritten["$ref"] = "/".join(parts)
                    return rewritten
                if isinstance(value, list):
                    return [_rewrite_server_refs(child) for child in value]
                return value

            # Copy components into the aggregate after the full local mapping is
            # known. This leaves each sub-app's cached OpenAPI object untouched.
            for comp_type, components in server_components.items():
                if not isinstance(components, dict):
                    continue
                target_components = base_spec["components"].setdefault(comp_type, {})
                for comp_name, comp_spec in components.items():
                    target_name = component_mapping[(comp_type, comp_name)]
                    target_components[target_name] = _rewrite_server_refs(comp_spec)

            # Merge paths with tool enable/disable filtering
            server_state = state_manager.get_server_state(server_name)
            tool_states = server_state["tools"]
            for path, path_spec in server_spec.get("paths", {}).items():
                if path in ["/docs", "/openapi.json"] or path.startswith("/openapi"):
                    continue
                if path.startswith("/") and path.count("/") == 1:
                    tool_name = path.lstrip("/")
                    if not tool_states.get(tool_name, True):
                        continue
                merged_path = mount_path + path if path != "/" else mount_path
                if merged_path in base_spec["paths"]:
                    merged_path = f"{mount_path}_{server_name}{path}"
                rewritten_path_spec = _rewrite_server_refs(path_spec)
                if isinstance(rewritten_path_spec, dict):
                    for method, operation in rewritten_path_spec.items():
                        if isinstance(operation, dict):
                            if "tags" not in operation:
                                operation["tags"] = []
                            if server_title not in operation["tags"]:
                                operation["tags"].append(server_title)
                base_spec["paths"][merged_path] = rewritten_path_spec
        except Exception as e:
            logger.error("Failed to process server %s for aggregation: %s", server_name, e)
            continue

    for tag_name in sorted(server_tags):
        base_spec["tags"].append({"name": tag_name, "description": f"Tools from {tag_name} server"})

    cache_data = {"schema": base_spec, "built_at": datetime.now(timezone.utc).isoformat()}
    setattr(main_app.state, cache_key, cache_data)
    setattr(main_app.state, "aggregate_openapi_dirty", False)
    return JSONResponse(content=base_spec)


@router.get("/rest-tools")
async def get_rest_tools_enabled(request: Request):
    """Return whether REST-side (port 8000) tool endpoints are globally enabled."""
    main_app = request.app
    state_manager = main_app.state.state_manager
    return {"ok": True, "enabled": state_manager.is_rest_tools_enabled()}


@router.post("/rest-tools/enable")
async def enable_rest_tools(request: Request):
    """Expose REST tools without changing server runtimes."""
    main_app = request.app
    if getattr(main_app.state, 'read_only_mode', False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    state_manager = main_app.state.state_manager
    state_manager.set_rest_tools_enabled(True)
    main_app.state.aggregate_openapi_dirty = True
    logger.info("REST tools globally enabled; server runtimes unchanged")
    return {"ok": True, "enabled": True}


@router.post("/rest-tools/disable")
async def disable_rest_tools(request: Request):
    """Hide and block REST tools without changing server runtimes."""
    main_app = request.app
    if getattr(main_app.state, 'read_only_mode', False):
        return JSONResponse(status_code=403, content=error_envelope("Read-only mode enabled", code="read_only"))
    state_manager = main_app.state.state_manager
    state_manager.set_rest_tools_enabled(False)
    main_app.state.aggregate_openapi_dirty = True
    logger.info("REST tools globally disabled; server runtimes unchanged")
    return {"ok": True, "enabled": False}
