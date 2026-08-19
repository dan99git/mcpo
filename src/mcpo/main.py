import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Body, Query
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
# mcp 2.0: streamablehttp_client renamed to streamable_http_client and the
# headers= kwarg was removed; headers now travel on a pre-built httpx2 client
# (mcp/client/streamable_http.py:640, mcp/shared/_httpx_utils.py:23).
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


@asynccontextmanager
async def streamablehttp_client(url: str, headers=None):
    """Compat shim for the mcp<2 streamablehttp_client(url, headers=...) API.

    mcp 2.0 removed the headers kwarg; a caller-provided httpx2 AsyncClient
    carries them instead, and the caller owns that client's lifecycle.
    """
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as streams:
            yield streams

from mcpo.utils.auth import (
    APIKeyMiddleware,
    ModelAPIKeyMiddleware,
    get_verify_api_key,
)
from mcpo.middleware.request_limits import (
    ChatBodyLimitMiddleware,
    PackageArchiveBodyLimitMiddleware,
)
from mcpo.utils.main import (
    get_model_fields,
    get_tool_handler,
    normalize_server_type,
)
from mcpo.utils.config import (
    interpolate_env_placeholders_in_config,
    normalize_config_shape,
    replace_mcp_servers_preserving_shape,
)
from mcpo.utils.config_watcher import ConfigWatcher
from mcpo.services.state import StateSaveError, get_state_manager
from mcpo.services.model_api_keys import get_model_api_key_store, ModelAPIKeyStoreError
from mcpo.services.logging import get_log_manager
from mcpo.services.logging_handlers import BufferedLogHandler
from mcpo.services.file_logging import (
    reattach_uvicorn_file_handlers,
    setup_file_logging,
)

from mcpo.api.routers.admin import (
    _mount_or_remount_fastmcp,
    _shutdown_fastmcp_proxy,
    router as admin_router,
)
from mcpo.api.routers.chat import router as chat_router
from mcpo.api.routers.completions import router as completions_router
from mcpo.api.routers.model_api_keys import router as model_api_keys_router
from mcpo.api.routers.providers import router as providers_router
from mcpo.api.routers.health import (
    _health_state,
    _update_health_snapshot,
    register_health_endpoint as _register_health_endpoint,
)
from mcpo.api.routers.tools import create_dynamic_endpoints

# MCP protocol version for direct per-server ClientSession connections.
# The native proxy negotiates 2026-07-28 and legacy backends independently in
# mcpo/proxy.py. Keep this direct-client default legacy until that path negotiates.
MCP_VERSION = "2025-06-18"


logger = logging.getLogger(__name__)

def error_envelope(message: str, code: str | None = None, data: Any | None = None):
    payload = {"ok": False, "error": {"message": message}}
    if code:
        payload["error"]["code"] = code
    if data is not None:
        payload["error"]["data"] = data
    return payload


# State management now uses StateManager singleton - see services/state.py

# Global reload lock to ensure atomic config reloads
_reload_lock = asyncio.Lock()


def _get_config_transaction_lock(main_app: FastAPI) -> asyncio.Lock:
    """Return the app-wide lock covering config read, write, activate, and rollback."""
    lock = getattr(main_app.state, "config_transaction_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        main_app.state.config_transaction_lock = lock
    return lock


class ConfigTransactionMiddleware:
    """Serialize every HTTP configuration mutation as one control-plane transaction."""

    _EXACT_MUTATIONS = {
        ("POST", "/mcpo/post_config"),
        ("POST", "/_meta/servers"),
        ("POST", "/_meta/config/save"),
        ("POST", "/_meta/config/mcpServers/save"),
        ("POST", "/_meta/reload"),
    }

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        is_remove = method == "DELETE" and path.startswith("/_meta/servers/")
        if scope.get("type") == "http" and (
            (method, path) in self._EXACT_MUTATIONS or is_remove
        ):
            main_app = scope.get("app")
            async with _get_config_transaction_lock(main_app):
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)

# Global log buffer for UI display (mirrors centralized log manager)
_log_buffer: list[dict] = []
_log_buffer_lock = threading.Lock()
MAX_LOG_ENTRIES = 2000


class GracefulShutdown:
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.tasks = set()

    def handle_signal(self, sig, frame=None):
        """Handle shutdown signals gracefully"""
        logger.info(
            f"\nReceived {signal.Signals(sig).name}, initiating graceful shutdown..."
        )
        self.shutdown_event.set()

    def track_task(self, task):
        """Track tasks for cleanup"""
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


def validate_server_config(server_name: str, server_cfg: Dict[str, Any]) -> None:
    """Validate individual server configuration."""
    server_type = server_cfg.get("type")

    if normalize_server_type(server_type) in ("sse", "streamable-http"):
        if not server_cfg.get("url"):
            raise ValueError(f"Server '{server_name}' of type '{server_type}' requires a 'url' field")
    elif server_cfg.get("command"):
        # stdio server
        if not isinstance(server_cfg["command"], str):
            raise ValueError(f"Server '{server_name}' 'command' must be a string")
        if server_cfg.get("args") and not isinstance(server_cfg["args"], list):
            raise ValueError(f"Server '{server_name}' 'args' must be a list")
    elif server_cfg.get("url") and not server_type:
        # Fallback for old SSE config without explicit type
        pass
    else:
        raise ValueError(f"Server '{server_name}' must have either 'command' for stdio or 'type' and 'url' for remote servers")

    # Validate disabledTools
    disabled_tools = server_cfg.get("disabledTools")
    if disabled_tools is not None:
        if not isinstance(disabled_tools, list):
            raise ValueError(f"Server '{server_name}' 'disabledTools' must be a list")
        for tool_name in disabled_tools:
            if not isinstance(tool_name, str):
                raise ValueError(f"Server '{server_name}' 'disabledTools' must contain only strings")


def validate_config_data(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and validate parsed MCPO configuration data."""
    if not isinstance(config_data, dict):
        raise ValueError("Configuration must be a JSON object.")

    validated = normalize_config_shape(config_data)
    validated = interpolate_env_placeholders_in_config(validated)
    if "mcpServers" not in validated:
        raise ValueError("No 'mcpServers' found in config file.")

    mcp_servers = validated["mcpServers"]
    if not isinstance(mcp_servers, dict):
        raise ValueError("'mcpServers' must be an object.")

    for server_name, server_cfg in mcp_servers.items():
        if not isinstance(server_cfg, dict):
            raise ValueError(f"Server '{server_name}' configuration must be an object")
        validate_server_config(server_name, server_cfg)

    return validated


def load_config(config_path: str) -> Dict[str, Any]:
    """Load and validate config from file."""
    try:
        with open(config_path, "r") as f:
            config_data = json.load(f)
        return validate_config_data(config_data)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        raise
    except FileNotFoundError:
        logger.error(f"Config file not found: {config_path}")
        raise
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}")
        raise


def create_sub_app(server_name: str, server_cfg: Dict[str, Any], cors_allow_origins,
                   api_key: Optional[str], strict_auth: bool, api_dependency,
                   connection_timeout, lifespan) -> FastAPI:
    """Create a sub-application for an MCP server."""
    sub_app = FastAPI(
        title=f"{server_name}",
        description=f"{server_name} MCP Server\n\n- [back to tool list](/docs)",
        version="1.0",
        lifespan=lifespan,
    )

    sub_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins or ["*"],
        allow_credentials=cors_allow_origins is not None and cors_allow_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Configure server type and connection parameters
    if server_cfg.get("command"):
        # stdio
        sub_app.state.server_type = "stdio"
        sub_app.state.command = server_cfg["command"]
        sub_app.state.args = server_cfg.get("args", [])
        sub_app.state.env = {**os.environ, **server_cfg.get("env", {})}

    server_config_type = server_cfg.get("type")
    if normalize_server_type(server_config_type) == "sse" and server_cfg.get("url"):
        sub_app.state.server_type = normalize_server_type("sse")
        sub_app.state.args = [server_cfg["url"]]
        headers = dict(server_cfg.get("headers") or {})
        headers["MCP-Protocol-Version"] = MCP_VERSION
        sub_app.state.headers = headers
    elif normalize_server_type(server_config_type) == "streamable-http" and server_cfg.get("url"):
        url = server_cfg["url"]
        sub_app.state.server_type = normalize_server_type("streamable-http")
        sub_app.state.args = [url]
        headers = dict(server_cfg.get("headers") or {})
        headers["MCP-Protocol-Version"] = MCP_VERSION
        sub_app.state.headers = headers
    elif not server_config_type and server_cfg.get("url"):
        # Fallback for old SSE config
        sub_app.state.server_type = normalize_server_type("sse")
        sub_app.state.args = [server_cfg["url"]]
        headers = dict(server_cfg.get("headers") or {})
        headers["MCP-Protocol-Version"] = MCP_VERSION
        sub_app.state.headers = headers

    if api_key and strict_auth:
        sub_app.add_middleware(APIKeyMiddleware, api_key=api_key)

    sub_app.state.api_dependency = api_dependency
    sub_app.state.connection_timeout = connection_timeout
    sub_app.state.disabled_tools = server_cfg.get("disabledTools", [])
    # Keep a stable config key for operation_id generation and discovery
    sub_app.state.config_key = server_name

    return sub_app


def mount_config_servers(main_app: FastAPI, config_data: Dict[str, Any],
                        cors_allow_origins, api_key: Optional[str], strict_auth: bool,
                        api_dependency, connection_timeout, lifespan, path_prefix: str):
    """Mount only config/state-enabled MCP servers."""
    mcp_servers = config_data.get("mcpServers", {})
    state_manager = (
        getattr(main_app.state, "state_manager", None) or get_state_manager()
    )
    logger.info("Configuring MCP Servers:")
    for server_name, server_cfg in mcp_servers.items():
        if isinstance(server_cfg, dict) and not server_cfg.get("enabled", True):
            if state_manager.is_server_enabled(server_name):
                state_manager.set_server_enabled(server_name, False)
            logger.info("Skipping disabled server: '%s'", server_name)
            continue
        if not state_manager.is_server_enabled(server_name):
            logger.info("Skipping disabled server: '%s'", server_name)
            continue
        sub_app = create_sub_app(
            server_name, server_cfg, cors_allow_origins, api_key,
            strict_auth, api_dependency, connection_timeout, lifespan
        )
        sub_app.state.parent_app = main_app
        main_app.mount(f"{path_prefix}{server_name}", sub_app)


def _server_mount_path(main_app: FastAPI, server_name: str) -> str:
    return f"{getattr(main_app.state, 'path_prefix', '/')}{server_name}"

def _server_name_from_mount(route: Mount) -> str:
    """Return the configured server key independently of its URL prefix."""
    route_state = getattr(getattr(route, "app", None), "state", None)
    config_key = getattr(route_state, "config_key", None)
    if isinstance(config_key, str) and config_key:
        return config_key
    return str(getattr(route, "path", "")).strip("/")

def _find_server_mounts(main_app: FastAPI, server_name: str) -> list:
    mount_path = _server_mount_path(main_app, server_name)
    return [route for route in main_app.router.routes
            if isinstance(route, Mount) and getattr(route, "path", None) == mount_path]

def _remove_server_mounts(main_app: FastAPI, server_name: str) -> list:
    removed = _find_server_mounts(main_app, server_name)
    for route in removed:
        main_app.router.routes.remove(route)
    if removed:
        logger.info("Unmounted server: %s", server_name)
    return removed

def _create_configured_server_app(main_app: FastAPI, server_name: str) -> Optional[FastAPI]:
    config_data = getattr(main_app.state, "config_data", {}) or {}
    servers = config_data.get("mcpServers", {}) if isinstance(config_data, dict) else {}
    server_cfg = servers.get(server_name)
    if not isinstance(server_cfg, dict):
        return None
    sub_app = create_sub_app(
        server_name, server_cfg,
        getattr(main_app.state, "cors_allow_origins", ["*"]),
        getattr(main_app.state, "api_key", None),
        getattr(main_app.state, "strict_auth", False),
        getattr(main_app.state, "api_dependency", None),
        getattr(main_app.state, "connection_timeout", None),
        getattr(main_app.state, "lifespan", None),
    )
    sub_app.state.parent_app = main_app
    return sub_app

async def unmount_servers(main_app: FastAPI, path_prefix: str, server_names: list):
    """Unmount specific MCP servers.

    Tears down each server's runtime (session + transport + child, if any) via the
    server_runtimes registry and awaits it BEFORE removing the route, so the old
    child is dead before any replacement is mounted. Uniform across config-initial
    and dynamically-added servers -- there is one registry, not a special case per
    origin.
    """
    for server_name in server_names:
        result = await teardown_server_runtime(main_app, server_name)
        if not result.get("ok", True):
            error = result.get("error") or "unknown teardown error"
            logger.error(
                "Teardown for '%s' during unmount failed: %s",
                server_name,
                error,
            )
            raise RuntimeError(f"Teardown for '{server_name}' failed: {error}")

        mount_path = f"{path_prefix}{server_name}"

        # Find and remove the mount
        routes_to_remove = []
        for route in main_app.router.routes:
            if hasattr(route, 'path') and route.path == mount_path:
                routes_to_remove.append(route)

        for route in routes_to_remove:
            main_app.router.routes.remove(route)
            logger.info(f"Unmounted server: {server_name}")


async def reload_config_handler(main_app: FastAPI, new_config_data: Dict[str, Any]):
    """Activate one config generation and compensate changed runtimes on failure."""
    async with _reload_lock:
        old_config_data = deepcopy(
            getattr(main_app.state, "config_data", {})
        )
        backup_routes = list(main_app.router.routes)
        affected_names: list[str] = []
        previous_runtimes: Dict[str, Any] = {}
        previous_runtime_alive: set[str] = set()
        previous_runtime_connected: Dict[str, bool] = {}
        previous_server_enabled: Dict[str, bool] = {}
        state_manager = None

        try:
            old_servers = old_config_data.get("mcpServers", {})
            new_servers = new_config_data.get("mcpServers", {})
            old_names = list(old_servers)
            new_names = list(new_servers)
            old_name_set = set(old_names)
            new_name_set = set(new_names)

            servers_to_remove = [
                name for name in old_names if name not in new_name_set
            ]
            servers_to_update = [
                name
                for name in old_names
                if name in new_name_set
                and old_servers[name] != new_servers[name]
            ]
            servers_to_add = [
                name for name in new_names if name not in old_name_set
            ]
            activation_names = [
                name
                for name in new_names
                if name in set(servers_to_add) | set(servers_to_update)
            ]
            affected_names = [
                name
                for name in old_names
                if name in set(servers_to_remove) | set(servers_to_update)
            ] + servers_to_add

            cors_allow_origins = getattr(
                main_app.state,
                "cors_allow_origins",
                ["*"],
            )
            api_key = getattr(main_app.state, "api_key", None)
            strict_auth = getattr(main_app.state, "strict_auth", False)
            api_dependency = getattr(main_app.state, "api_dependency", None)
            connection_timeout = getattr(
                main_app.state,
                "connection_timeout",
                None,
            )
            lifespan = getattr(main_app.state, "lifespan", None)
            path_prefix = getattr(main_app.state, "path_prefix", "/")
            state_manager = (
                getattr(main_app.state, "state_manager", None) or get_state_manager()
            )

            runtime_registry = _get_server_runtimes(main_app)
            previous_runtimes = {
                name: runtime_registry[name]
                for name in affected_names
                if name in runtime_registry
            }
            previous_runtime_alive = {
                name
                for name, runtime in previous_runtimes.items()
                if _runtime_is_alive(runtime)
            }
            previous_runtime_connected = {
                name: bool(getattr(runtime, "connected", False))
                for name, runtime in previous_runtimes.items()
            }
            previous_server_enabled = {
                name: state_manager.is_server_enabled(name)
                for name in affected_names
            }

            candidate_apps: Dict[str, FastAPI] = {}
            for server_name in activation_names:
                server_cfg = new_servers[server_name]
                if (
                    isinstance(server_cfg, dict)
                    and not server_cfg.get("enabled", True)
                ):
                    continue
                if not state_manager.is_server_enabled(server_name):
                    continue
                sub_app = create_sub_app(
                    server_name,
                    server_cfg,
                    cors_allow_origins,
                    api_key,
                    strict_auth,
                    api_dependency,
                    connection_timeout,
                    lifespan,
                )
                sub_app.state.parent_app = main_app
                candidate_apps[server_name] = sub_app

            if servers_to_remove:
                logger.info("Removing servers: %s", servers_to_remove)
                await unmount_servers(
                    main_app,
                    path_prefix,
                    servers_to_remove,
                )

            if servers_to_update:
                logger.info("Updating servers: %s", servers_to_update)
                await unmount_servers(
                    main_app,
                    path_prefix,
                    servers_to_update,
                )

            if activation_names:
                logger.info("Adding or updating servers: %s", activation_names)

            for server_name in activation_names:
                server_cfg = new_servers[server_name]
                if (
                    isinstance(server_cfg, dict)
                    and not server_cfg.get("enabled", True)
                ):
                    state_manager.set_server_enabled(server_name, False)
                if not state_manager.is_server_enabled(server_name):
                    logger.info(
                        "Server '%s' added or updated but disabled; "
                        "leaving it unmounted.",
                        server_name,
                    )
                    continue

                sub_app = candidate_apps.get(server_name)
                if sub_app is None:
                    raise RuntimeError(
                        f"Candidate app for '{server_name}' was not prepared"
                    )
                main_app.mount(
                    f"{path_prefix}{server_name}",
                    sub_app,
                )
                current_state = state_manager.get_server_state(server_name)
                state_manager.set_server_enabled(
                    server_name,
                    current_state.get("enabled", True),
                )

                try:
                    runtime = await spawn_server_runtime(
                        main_app,
                        server_name,
                        sub_app,
                        api_dependency=api_dependency,
                    )
                    if runtime.connected:
                        logger.info(
                            "Successfully connected to new server: '%s'",
                            server_name,
                        )
                        sub_app.state.last_error = None
                    else:
                        logger.warning(
                            "Failed to connect to new server: '%s'",
                            server_name,
                        )
                        sub_app.state.last_error = runtime.last_error
                except Exception as init_err:  # pragma: no cover - defensive
                    sub_app.state.is_connected = False
                    sub_app.state.last_error = str(init_err)
                    logger.error(
                        "Failed to initialize server '%s': %s. "
                        "Server remains mounted but marked disconnected.",
                        server_name,
                        init_err,
                    )

            main_app.state.config_data = new_config_data
            _health_state["generation"] += 1
            _health_state["last_reload"] = datetime.now(
                timezone.utc
            ).isoformat()
            _update_health_snapshot(main_app)
            main_app.state.aggregate_openapi_dirty = True
            logger.info("Config reload completed successfully")
        except Exception as update_error:
            logger.error(
                "Error during config reload, restoring previous generation: %s",
                update_error,
            )
            try:
                await _restore_reload_snapshot(
                    main_app,
                    old_config_data=old_config_data,
                    backup_routes=backup_routes,
                    affected_names=affected_names,
                    previous_runtimes=previous_runtimes,
                    previous_runtime_alive=previous_runtime_alive,
                    previous_runtime_connected=previous_runtime_connected,
                    previous_server_enabled=previous_server_enabled,
                    state_manager=state_manager,
                )
            except Exception as rollback_error:
                raise ConfigRollbackError(
                    update_error,
                    rollback_error,
                ) from update_error
            raise


class ConfigRollbackError(RuntimeError):
    """Configuration activation failed and the previous state could not be restored."""

    def __init__(self, update_error: Exception, rollback_error: Exception):
        self.update_error = update_error
        self.rollback_error = rollback_error
        super().__init__(
            f"update failed: {update_error}; rollback failed: {rollback_error}"
        )


async def _restore_reload_snapshot(
    main_app: FastAPI,
    *,
    old_config_data: Dict[str, Any],
    backup_routes: list,
    affected_names: list[str],
    previous_runtimes: Dict[str, Any],
    previous_runtime_alive: set[str],
    previous_runtime_connected: Dict[str, bool],
    previous_server_enabled: Dict[str, bool],
    state_manager: Any,
) -> None:
    """Restore the exact prior route/runtime generation after a partial reload."""
    affected_set = set(affected_names)
    main_app.state.config_data = old_config_data
    main_app.router.routes[:] = [
        route
        for route in main_app.router.routes
        if not (
            isinstance(route, Mount)
            and _server_name_from_mount(route) in affected_set
        )
    ]
    runtime_registry = _get_server_runtimes(main_app)
    replacement_routes: Dict[str, Mount] = {}
    compensation_runtime_names: list[str] = []

    try:
        for server_name in affected_names:
            previous_runtime = previous_runtimes.get(server_name)
            current_runtime = runtime_registry.get(server_name)
            previous_was_alive = server_name in previous_runtime_alive
            previous_was_connected = previous_runtime_connected.get(
                server_name,
                False,
            )
            prior_runtime_retained = (
                current_runtime is previous_runtime
                and previous_was_alive
                and _runtime_is_alive(current_runtime)
                and (
                    not previous_was_connected
                    or bool(getattr(current_runtime, "connected", False))
                )
            )
            if prior_runtime_retained:
                continue

            if current_runtime is not None:
                teardown_result = await teardown_server_runtime(
                    main_app,
                    server_name,
                )
                if not teardown_result.get("ok", True):
                    error = (
                        teardown_result.get("error")
                        or "unknown teardown error"
                    )
                    raise RuntimeError(
                        f"Rollback teardown for '{server_name}' failed: {error}"
                    )

        if state_manager is not None:
            for server_name, was_enabled in previous_server_enabled.items():
                if state_manager.is_server_enabled(server_name) != was_enabled:
                    state_manager.set_server_enabled(
                        server_name,
                        was_enabled,
                    )

        for server_name in affected_names:
            previous_runtime = previous_runtimes.get(server_name)
            current_runtime = runtime_registry.get(server_name)
            if (
                current_runtime is previous_runtime
                and server_name in previous_runtime_alive
                and _runtime_is_alive(current_runtime)
                and (
                    not previous_runtime_connected.get(server_name, False)
                    or bool(getattr(current_runtime, "connected", False))
                )
            ):
                continue
            if server_name not in previous_runtime_alive:
                continue

            old_routes = [
                route
                for route in backup_routes
                if isinstance(route, Mount)
                and _server_name_from_mount(route) == server_name
            ]
            if not old_routes:
                raise RuntimeError(
                    f"Previous route for server '{server_name}' is missing"
                )

            sub_app = _create_configured_server_app(main_app, server_name)
            if sub_app is None:
                raise RuntimeError(
                    f"Cannot reconstruct previous server '{server_name}'"
                )
            restored_runtime = await spawn_server_runtime(
                main_app,
                server_name,
                sub_app,
                api_dependency=getattr(
                    main_app.state,
                    "api_dependency",
                    None,
                ),
            )
            compensation_runtime_names.append(server_name)
            if (
                not _runtime_is_alive(restored_runtime)
                or (
                    previous_runtime_connected.get(server_name, False)
                    and not bool(
                        getattr(restored_runtime, "connected", False)
                    )
                )
            ):
                raise RuntimeError(
                    getattr(restored_runtime, "last_error", None)
                    or f"Failed to reconnect previous server '{server_name}'"
                )

            old_route = old_routes[0]
            replacement_routes[server_name] = Mount(
                old_route.path,
                app=sub_app,
                name=getattr(old_route, "name", None),
            )

        restored_routes = []
        emitted_replacements: set[str] = set()
        for route in backup_routes:
            if isinstance(route, Mount):
                server_name = _server_name_from_mount(route)
                replacement = replacement_routes.get(server_name)
                if replacement is not None:
                    if server_name not in emitted_replacements:
                        restored_routes.append(replacement)
                        emitted_replacements.add(server_name)
                    continue
            restored_routes.append(route)
        main_app.router.routes[:] = restored_routes
    except Exception:
        for server_name in reversed(compensation_runtime_names):
            try:
                await teardown_server_runtime(main_app, server_name)
            except Exception:
                logger.error(
                    "Failed to clean compensation runtime '%s'",
                    server_name,
                    exc_info=True,
                )
        raise

def _read_config_file_snapshot(config_path: str) -> Optional[bytes]:
    try:
        return Path(config_path).read_bytes()
    except FileNotFoundError:
        return None


def _atomic_write_config(config_path: str, content: str) -> None:
    """Replace a config file only after its complete contents reach disk."""
    destination = Path(config_path)
    fd, temp_path = tempfile.mkstemp(
        dir=str(destination.parent),
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            fd = -1
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, destination)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.warning(
                "Failed to remove temporary config file '%s': %s",
                temp_path,
                cleanup_error,
            )
        raise


async def _reload_config_with_rollback(
    main_app: FastAPI,
    new_config_data: Dict[str, Any],
    previous_config_data: Dict[str, Any],
    config_path: str,
    previous_file_bytes: Optional[bytes],
) -> None:
    """Activate persisted config, restoring file and runtime if activation fails."""
    handler_committed = False
    try:
        await reload_config_handler(main_app, new_config_data)
        handler_committed = True
        await _mount_or_remount_fastmcp(main_app, base_path="/mcp")
    except Exception as update_error:
        try:
            path = Path(config_path)
            if previous_file_bytes is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous_file_bytes)
        except Exception as file_rollback_error:
            raise ConfigRollbackError(
                update_error,
                file_rollback_error,
            ) from update_error

        if not handler_committed:
            raise

        try:
            await reload_config_handler(
                main_app,
                deepcopy(previous_config_data),
            )
            await _mount_or_remount_fastmcp(main_app, base_path="/mcp")
        except Exception as rollback_error:
            raise ConfigRollbackError(
                update_error,
                rollback_error,
            ) from update_error
        raise

@dataclass
class ServerRuntime:
    """Owns one MCP server's live connection: the transport, the ClientSession, and
    the background task that holds both open until asked to stop.

    One instance covers a server regardless of whether it was mounted from the
    initial config or added later (add-server, reload, reinit). There is a single
    registry (``main_app.state.server_runtimes``) covering both origins, not a
    separate mechanism per origin.
    """
    name: str
    task: Optional[asyncio.Task] = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    session: Optional[ClientSession] = None
    connected: bool = False
    last_error: Optional[str] = None


def _get_server_runtimes(main_app: FastAPI) -> Dict[str, "ServerRuntime"]:
    if not hasattr(main_app.state, "server_runtimes"):
        main_app.state.server_runtimes = {}
    return main_app.state.server_runtimes


def _runtime_is_alive(runtime: Optional["ServerRuntime"]) -> bool:
    task = getattr(runtime, "task", None)
    return task is not None and not task.done()


def _fastmcp_proxy_is_initialized(main_app: FastAPI) -> bool:
    """Return whether this app has ever mounted its in-process native proxy."""
    if hasattr(main_app.state, "fastmcp_proxy_mounts"):
        return True
    if hasattr(main_app.state, "fastmcp_proxy_lifespans"):
        return True
    return any(
        bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
        for route in main_app.router.routes
    )


async def _sync_initialized_fastmcp_proxy(main_app: FastAPI) -> None:
    """Rebuild native MCP mounts only when that optional surface is active."""
    if _fastmcp_proxy_is_initialized(main_app):
        await _mount_or_remount_fastmcp(main_app, base_path="/mcp")


async def _reload_runtime_surfaces_with_rollback(
    main_app: FastAPI,
    new_config_data: Dict[str, Any],
) -> None:
    """Activate an external file edit without rewriting that external source."""
    previous_config_data = deepcopy(
        getattr(main_app.state, "config_data", {"mcpServers": {}})
    )
    handler_committed = False
    try:
        await reload_config_handler(main_app, new_config_data)
        handler_committed = True
        await _sync_initialized_fastmcp_proxy(main_app)
    except Exception as update_error:
        if not handler_committed:
            raise
        try:
            await reload_config_handler(
                main_app,
                deepcopy(previous_config_data),
            )
            await _sync_initialized_fastmcp_proxy(main_app)
        except Exception as rollback_error:
            raise ConfigRollbackError(
                update_error,
                rollback_error,
            ) from update_error
        raise

async def _server_runtime_main(runtime: "ServerRuntime", sub_app: FastAPI, api_dependency) -> None:
    """Connect, register tool endpoints, signal readiness, then hold the connection
    open until ``runtime.stop_event`` is set.

    The client context managers (which terminate the child process / transport on
    exit) are entered and exited by this SAME task throughout, as the mcp client
    library requires -- there is no cross-task cancel-scope violation.
    """
    server_type = normalize_server_type(getattr(sub_app.state, "server_type", "stdio"))
    command = getattr(sub_app.state, "command", None)
    args = getattr(sub_app.state, "args", [])
    args = args if isinstance(args, list) else [args]
    env = getattr(sub_app.state, "env", {})
    connection_timeout = getattr(sub_app.state, "connection_timeout", 10)

    sub_app.state.is_connected = False
    try:
        if server_type == "stdio":
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **env},
            )
            client_context = stdio_client(server_params)
        elif server_type == "sse":
            headers = getattr(sub_app.state, "headers", None)
            client_context = sse_client(
                url=args[0],
                sse_read_timeout=connection_timeout or 900,
                headers=headers,
            )
        elif server_type == "streamable-http":
            headers = getattr(sub_app.state, "headers", None)
            client_context = streamablehttp_client(url=args[0], headers=headers)
        else:
            raise ValueError(f"Unsupported server type: {server_type}")

        async with client_context as (reader, writer, *_):
            async with ClientSession(reader, writer) as session:
                sub_app.state.session = session
                runtime.session = session
                await create_dynamic_endpoints(sub_app, api_dependency=api_dependency)
                sub_app.state.is_connected = True
                runtime.connected = True
                runtime.last_error = None
                runtime.ready_event.set()
                await runtime.stop_event.wait()
        # Both context managers have exited in this task: the transport (and, for
        # stdio, the child process) is fully torn down here before we return.
    except asyncio.CancelledError:
        raise
    except Exception as e:
        runtime.connected = False
        runtime.last_error = f"{type(e).__name__}: {e}"
        sub_app.state.is_connected = False
        logger.error(
            f"Server runtime for '{runtime.name}' failed: {type(e).__name__}: {e}",
            exc_info=True,
        )
    finally:
        runtime.connected = False
        sub_app.state.is_connected = False
        sub_app.state.session = None
        runtime.session = None
        # Always unblock a waiting spawn_server_runtime call, success or failure.
        runtime.ready_event.set()


async def spawn_server_runtime(
    main_app: FastAPI,
    server_name: str,
    sub_app: FastAPI,
    *,
    api_dependency=None,
    timeout: Optional[float] = None,
) -> "ServerRuntime":
    """Start the persistent connection manager task for one server, or return the
    existing one if it is already alive.

    This is the single duplicate-spawn guard: callers never need to check whether a
    server is already running before calling this -- it is safe to call repeatedly.
    """
    runtimes = _get_server_runtimes(main_app)
    existing = runtimes.get(server_name)
    if existing is not None and existing.task is not None and not existing.task.done():
        logger.info(f"Server runtime for '{server_name}' already running; skipping duplicate spawn")
        return existing

    runtime = ServerRuntime(name=server_name)
    runtimes[server_name] = runtime
    raw_timeout = timeout if timeout is not None else getattr(sub_app.state, "connection_timeout", None)
    try:
        effective_timeout = float(raw_timeout) if raw_timeout else 30
    except (TypeError, ValueError):
        effective_timeout = 30
    runtime.task = asyncio.create_task(
        _server_runtime_main(runtime, sub_app, api_dependency),
        name=f"mcp-server-runtime:{server_name}",
    )
    try:
        await asyncio.wait_for(runtime.ready_event.wait(), timeout=effective_timeout)
    except asyncio.TimeoutError:
        runtime.last_error = f"Timed out waiting for '{server_name}' to connect after {effective_timeout}s"
        logger.error(runtime.last_error)
    return runtime


async def teardown_server_runtime(
    main_app: FastAPI,
    server_name: str,
    *,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Stop the persistent connection manager task for one server, if any.

    Idempotent: tearing down a server with no active runtime is a no-op success.
    Signals the task to stop and awaits it; if it does not exit within ``timeout``,
    the task is cancelled and the failure is reported -- never a silent hang.
    """
    runtimes = _get_server_runtimes(main_app)
    runtime = runtimes.get(server_name)
    if runtime is None or runtime.task is None or runtime.task.done():
        runtimes.pop(server_name, None)
        return {"ok": True, "server": server_name, "was_running": False, "forced": False, "error": None}

    runtime.stop_event.set()
    forced = False
    error = None
    try:
        await asyncio.wait_for(asyncio.shield(runtime.task), timeout=timeout)
    except asyncio.TimeoutError:
        forced = True
        logger.error(f"Server runtime for '{server_name}' did not stop within {timeout}s; cancelling task")
        runtime.task.cancel()
        try:
            await asyncio.wait_for(runtime.task, timeout=5.0)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            error = f"Server runtime for '{server_name}' did not respond to cancellation; abandoning task"
            logger.error(error)
        except Exception as e:
            error = f"Server runtime for '{server_name}' raised during forced cancel: {type(e).__name__}: {e}"
            logger.error(error, exc_info=True)
        else:
            error = f"Teardown of '{server_name}' timed out after {timeout}s; task was force-cancelled"
    except Exception as e:
        error = f"Server runtime for '{server_name}' raised during teardown: {type(e).__name__}: {e}"
        logger.error(error, exc_info=True)

    if error is not None and _runtime_is_alive(runtime):
        # A task that ignored cancellation is still live. Keep it registered so
        # callers can restore routing without starting a duplicate child process.
        return {"ok": False, "server": server_name, "was_running": True, "forced": forced, "error": error}

    runtimes.pop(server_name, None)
    return {"ok": error is None, "server": server_name, "was_running": True, "forced": forced, "error": error}


async def _restore_server_after_failed_disable(
    main_app: FastAPI,
    server_name: str,
    backup_routes: list,
    removed_routes: list,
) -> None:
    """Restore one server with a live runtime and its original route position."""
    current_runtime = _get_server_runtimes(main_app).get(server_name)
    if _runtime_is_alive(current_runtime) and current_runtime.connected:
        main_app.router.routes = backup_routes
        return

    sub_app = _create_configured_server_app(main_app, server_name)
    if sub_app is None:
        raise RuntimeError(f"Cannot restore missing server '{server_name}'")
    runtime = await spawn_server_runtime(
        main_app,
        server_name,
        sub_app,
        api_dependency=getattr(main_app.state, "api_dependency", None),
    )
    if not runtime.connected:
        await teardown_server_runtime(main_app, server_name)
        raise RuntimeError(runtime.last_error or f"Failed to restore server '{server_name}'")

    main_app.mount(_server_mount_path(main_app, server_name), sub_app)
    replacement = _find_server_mounts(main_app, server_name)[-1]
    main_app.router.routes.remove(replacement)
    insertion_index = min(
        (backup_routes.index(route) for route in removed_routes),
        default=len(backup_routes),
    )
    restored_routes = [route for route in backup_routes if route not in removed_routes]
    restored_routes.insert(min(insertion_index, len(restored_routes)), replacement)
    main_app.router.routes = restored_routes


async def initialize_sub_app(sub_app: FastAPI):
    """Initialize a mounted sub-app by spawning its persistent connection runtime.

    Callable on-demand after dynamic (re)mounts (e.g. from /_meta/reinit). Delegates
    to spawn_server_runtime so the resulting connection is held open by a background
    task rather than being torn down as soon as this function returns.
    """
    if getattr(sub_app.state, 'is_connected', False):
        # Already initialized
        return
    main_app = getattr(sub_app.state, 'parent_app', None)
    if main_app is None:
        raise ValueError("initialize_sub_app requires sub_app.state.parent_app to be set")
    api_dependency = getattr(sub_app.state, 'api_dependency', None)
    server_name = getattr(sub_app.state, 'config_key', None) or sub_app.title
    # Remove old tool endpoints if any (POST /toolName at root)
    retained = []
    for r in sub_app.router.routes:
        methods = getattr(r, 'methods', set())
        path = getattr(r, 'path', '')
        if 'POST' in methods and path.count('/') == 1 and path not in ('/openapi.json', '/docs'):
            # drop tool endpoint
            continue
        retained.append(r)
    sub_app.router.routes = retained
    await spawn_server_runtime(main_app, server_name, sub_app, api_dependency=api_dependency)


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_type = normalize_server_type(getattr(app.state, "server_type", "stdio"))
    command = getattr(app.state, "command", None)
    args = getattr(app.state, "args", [])
    args = args if isinstance(args, list) else [args]
    env = getattr(app.state, "env", {})
    connection_timeout = getattr(app.state, "connection_timeout", 10)
    api_dependency = getattr(app.state, "api_dependency", None)
    path_prefix = getattr(app.state, "path_prefix", "/")

    # Get shutdown handler from app state
    shutdown_handler = getattr(app.state, "shutdown_handler", None)

    is_main_app = not command and not (server_type in ["sse", "streamable-http"] and args)

    if is_main_app:
        successful_servers = []
        failed_servers = []
        skipped_servers = []

        state_manager = get_state_manager()
        config_data = getattr(app.state, "config_data", {}) or {}
        mcp_servers_cfg = config_data.get("mcpServers", {}) if isinstance(config_data, dict) else {}
        main_api_dependency = getattr(app.state, "api_dependency", None)
        mounted_servers = [
            (_server_name_from_mount(route), route.app)
            for route in app.routes
            if isinstance(route, Mount) and isinstance(route.app, FastAPI)
        ]

        for server_name, sub_app in mounted_servers:
            # Skip internal MCPO management server - it doesn't need MCP connection
            if sub_app.title == "MCPO Management Server":
                logger.info(f"Skipping connection for internal server: '{server_name}' (already available)")
                successful_servers.append(server_name)
                continue

            server_cfg = mcp_servers_cfg.get(server_name, {})
            if isinstance(server_cfg, dict) and not server_cfg.get("enabled", True):
                # Config-level disable always wins; keep persisted state in sync.
                state_manager.set_server_enabled(server_name, False)

            if not state_manager.is_server_enabled(server_name):
                logger.info(f"Skipping connection for disabled server: '{server_name}'")
                sub_app.state.is_connected = False
                sub_app.state.last_error = "Server disabled"
                skipped_servers.append(server_name)
                continue

            logger.info(f"Initiating connection for server: '{server_name}'...")
            try:
                runtime = await spawn_server_runtime(
                    app, server_name, sub_app, api_dependency=main_api_dependency
                )
                if runtime.connected:
                    logger.info(f"Successfully connected to '{server_name}'.")
                    successful_servers.append(server_name)
                    sub_app.state.last_error = None
                else:
                    logger.warning(
                        f"Connection attempt for '{server_name}' finished, but status is not 'connected'."
                    )
                    sub_app.state.last_error = runtime.last_error
                    failed_servers.append(server_name)
            except Exception as e:
                error_class_name = type(e).__name__
                if error_class_name == 'ExceptionGroup' or (hasattr(e, 'exceptions') and hasattr(e, 'message')):
                    logger.error(
                        f"Failed to establish connection for server: '{server_name}' - Multiple errors occurred:"
                    )
                    # Log each individual exception from the group
                    exceptions = getattr(e, 'exceptions', [])
                    for idx, exc in enumerate(exceptions):
                        logger.error(f"  Error {idx + 1}: {type(exc).__name__}: {exc}")
                        # Also log traceback for each exception
                        if hasattr(exc, '__traceback__'):
                            import traceback
                            tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
                            for line in tb_lines:
                                logger.debug(f"    {line.rstrip()}")
                else:
                    logger.error(
                        f"Failed to establish connection for server: '{server_name}' - {type(e).__name__}: {e}",
                        exc_info=True
                    )
                failed_servers.append(server_name)

        logger.info("\n--- Server Startup Summary ---")
        if successful_servers:
            logger.info("Successfully connected to:")
            for name in successful_servers:
                logger.info(f"  - {name}")
            app.description += "\n\n- **available tools**："
            for name in successful_servers:
                docs_path = urljoin(path_prefix, f"{name}/docs")
                app.description += f"\n    - [{name}]({docs_path})"
        if failed_servers:
            logger.warning("Failed to connect to:")
            for name in failed_servers:
                logger.warning(f"  - {name}")
        if skipped_servers:
            logger.info("Skipped (disabled):")
            for name in skipped_servers:
                logger.info(f"  - {name}")
        logger.info("--------------------------\n")

        if not successful_servers:
            logger.error("No MCP servers could be reached.")

        try:
            yield
        finally:
            # Tear down every server runtime (config-initial and dynamic alike) so no
            # child process outlives this process.
            for name in list(_get_server_runtimes(app).keys()):
                result = await teardown_server_runtime(app, name)
                if not result.get("ok", True):
                    logger.error(
                        f"Shutdown teardown for '{name}' reported an error: {result.get('error')}"
                    )
            # Serialize native proxy shutdown with config watchers and remount calls.
            await _shutdown_fastmcp_proxy(app)
    else:
        # This is a sub-app's lifespan
        app.state.is_connected = False
        try:
            if server_type == "stdio":
                server_params = StdioServerParameters(
                    command=command,
                    args=args,
                    env={**os.environ, **env},
                )
                client_context = stdio_client(server_params)
            elif server_type == "sse":
                headers = getattr(app.state, "headers", None)
                client_context = sse_client(
                    url=args[0],
                    sse_read_timeout=connection_timeout or 900,
                    headers=headers,
                )
            elif server_type == "streamable-http":
                headers = getattr(app.state, "headers", None)
                client_context = streamablehttp_client(url=args[0], headers=headers)
            else:
                raise ValueError(f"Unsupported server type: {server_type}")

            async with client_context as (reader, writer, *_):
                async with ClientSession(reader, writer) as session:
                    app.state.session = session
                    await create_dynamic_endpoints(app, api_dependency=api_dependency)
                    app.state.is_connected = True
                    yield
        except Exception as e:
            # Log the full exception with traceback for debugging
            logger.error(f"Failed to connect to MCP server '{app.title}': {type(e).__name__}: {e}", exc_info=True)
            app.state.is_connected = False
            # Re-raise the exception so it propagates to the main app's lifespan
            raise


async def create_internal_mcpo_server(main_app: FastAPI, api_dependency) -> FastAPI:
    """Create internal MCPO MCP server exposing management tools."""
    mcpo_app = FastAPI(
        title="MCPO Management Server",
        description="Internal MCP server exposing MCPO management capabilities",
        version="1.0",
        servers=[{"url": "/mcpo"}],
        dependencies=[Depends(api_dependency)] if api_dependency else [],
    )
    # Request models for clear OpenAPI requestBody schemas
    class PostConfigBody(BaseModel):
        config: Any

    class PostEnvBody(BaseModel):
        env_vars: Dict[str, Any]

    class PostRequirementsBody(BaseModel):
        content: str

    class InstallPythonPackageBody(BaseModel):
        package_name: str

    
    # Store reference to main app for accessing state
    mcpo_app.state.main_app = main_app

    def _internal_read_only() -> bool:
        return bool(getattr(mcpo_app.state.main_app.state, "read_only_mode", False))

    def _read_only_error() -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content=error_envelope("Read-only mode enabled", code="read_only"),
        )

    @mcpo_app.post(
        "/install_python_package",
        summary="Install Python Package",
        description=(
            "Install exactly one named package. \n\n"
            "Rules:\n"
            "- Single package only; no bulk installs.\n"
            "- Do not add upgrade flags (e.g., -U/--upgrade) unless explicitly requested.\n"
            "- Do not install extras or transitive tools beyond what is requested.\n"
            "- If installation fails, report the error and stop. Do not retry with guesses."
        ),
        operation_id="mcpo.install_python_package",
        openapi_extra={
            "x-instructions": [
                "Install only the provided package_name.",
                "No upgrades or version changes unless explicitly specified by the user.",
                "No additional flags or extras.",
                "On error, return the error; do not attempt speculative fixes."
            ]
        },
    )
    async def install_python_package(request: Request, body: InstallPythonPackageBody):
        """Install a Python package using pip."""
        if _internal_read_only():
            return _read_only_error()
        try:
            package_name = body.package_name if body else None
            if not package_name or not isinstance(package_name, str):
                logger.warning("install_python_package: Missing package_name parameter")
                return JSONResponse(status_code=422, content={"ok": False, "error": {"message": "Missing package_name"}})

            # Validate package name: alphanumeric, hyphens, underscores, dots, and optional version spec
            import re as _re
            if not _re.match(r'^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(\[.*\])?(([<>=!~]=?|@)[A-Za-z0-9._*+-]*)*$', package_name):
                logger.warning(f"install_python_package: Invalid package name '{package_name}'")
                return JSONResponse(status_code=422, content={"ok": False, "error": {"message": f"Invalid package name: {package_name}"}})

            logger.info(f"install_python_package: Starting installation of package '{package_name}'")

            # Run pip install
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_name],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode == 0:
                logger.info(f"install_python_package: Successfully installed package '{package_name}'")
                return {"ok": True, "message": f"Successfully installed {package_name}", "output": result.stdout}
            else:
                logger.error(
                    f"install_python_package: Failed to install package '{package_name}' - {result.stderr}"
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "error": {
                            "message": f"Failed to install {package_name}",
                            "details": result.stderr,
                        },
                    },
                )

        except subprocess.TimeoutExpired:
            logger.error(
                f"install_python_package: Installation of package '{package_name}' timed out after 120 seconds"
            )
            return JSONResponse(
                status_code=408, content={"ok": False, "error": {"message": "Installation timed out"}}
            )
        except Exception as e:
            logger.error(f"install_python_package: Installation failed with exception - {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": {"message": f"Installation failed: {str(e)}"}},
            )

    @mcpo_app.get(
        "/get_config",
        summary="Get Configuration",
        description=(
            "Read-only retrieval of the current mcpo.json. Use this to fetch the source of truth "
            "before proposing any changes."
        ),
        operation_id="mcpo.get_config",
        openapi_extra={
            "x-instructions": [
                "This endpoint is read-only.",
                "Call this before any configuration change to obtain the exact current object.",
                "Do not infer, normalize, or fix values; if something seems wrong, ask instead."
            ]
        },
    )
    async def get_config_get(request: Request):
        try:
            main_app = mcpo_app.state.main_app
            config_path = getattr(main_app.state, 'config_path', None)
            if not config_path:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": {"message": "No config file configured"}},
                )
            with open(config_path, 'r', encoding='utf-8') as f:
                config_content = f.read()
            config_data = json.loads(config_content)
            return {"config": config_data}
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": {"message": f"Failed to get config: {str(e)}"}},
            )

    # legacy POST /get_config removed to enforce GET-only for reads
    
    @mcpo_app.post(
        "/post_config",
        summary="Update Configuration",
        description=(
            "Apply the minimal, explicitly requested change to the configuration and reload. \n\n"
            "Process:\n"
            "1) First GET /mcpo/get_config and start from the returned object.\n"
            "2) Modify only the fields explicitly requested.\n"
            "3) Preserve all other fields exactly; no reformatting, reordering, or inferred fixes.\n"
            "4) Post a valid JSON object (no comments). On validation error, stop and report."
        ),
        operation_id="mcpo.post_config",
        openapi_extra={
            "x-instructions": [
                "Always read current config first via GET /mcpo/get_config.",
                "Perform minimal diffs only; do not touch unrelated fields.",
                "Preserve structure and values verbatim for untouched areas.",
                "No speculative fixes or improvements; ask if unsure.",
                "Submit a valid JSON object; if an error occurs, stop and report."
            ]
        },
    )
    async def post_config(request: Request, body: PostConfigBody):
        """Update MCPO configuration through the shared activation transaction."""
        if _internal_read_only():
            return _read_only_error()
        main_app = mcpo_app.state.main_app
        config_path = getattr(main_app.state, "config_path", None)
        if not config_path:
            return JSONResponse(
                status_code=400,
                content=error_envelope("No config file configured", code="no_config"),
            )

        raw_config = body.config
        if raw_config is None:
            return JSONResponse(
                status_code=422,
                content=error_envelope("Missing config data", code="invalid"),
            )
        try:
            if isinstance(raw_config, str):
                raw_config = json.loads(raw_config)
            config_data = validate_config_data(raw_config)
        except json.JSONDecodeError as exc:
            return JSONResponse(
                status_code=422,
                content=error_envelope("Invalid JSON", data=str(exc), code="invalid"),
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=422,
                content=error_envelope(str(exc), code="invalid"),
            )

        previous_config_data = deepcopy(
            getattr(main_app.state, "config_data", {"mcpServers": {}})
        )
        previous_file_bytes = _read_config_file_snapshot(config_path)
        backup_path = f"{config_path}.backup"
        try:
            if os.path.exists(config_path):
                import shutil

                shutil.copy2(config_path, backup_path)
            _atomic_write_config(config_path, json.dumps(raw_config, indent=2))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content=error_envelope(
                    "Failed to write config", data=str(exc), code="io_error"
                ),
            )

        try:
            await _reload_config_with_rollback(
                main_app,
                config_data,
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
                    "Reload failed", data=str(exc), code="reload_failed"
                ),
            )

        main_app.state.aggregate_openapi_dirty = True
        return {
            "ok": True,
            "message": "Configuration updated and reloaded",
            "backup": backup_path,
        }
    @mcpo_app.get(
        "/get_logs",
        summary="Get Server Logs",
        description="Read-only access to recent server log entries.",
        operation_id="mcpo.get_logs",
        openapi_extra={
            "x-instructions": [
                "Use logs for observation only.",
                "Keep limits small (default 20).",
                "Do not trigger writes based on interpretations without explicit instruction."
            ]
        },
    )
    async def get_logs_get(request: Request, limit: int = Query(20, ge=1, le=100)):
        """Get recent server logs."""
        try:
            log_manager = get_log_manager(MAX_LOG_ENTRIES)
            recent_logs = log_manager.get_logs(source="openapi", limit=limit)

            return {"ok": True, "logs": recent_logs, "count": len(recent_logs)}
        except Exception as e:
            return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Failed to get logs: {str(e)}"}})

    # legacy POST /get_logs removed to enforce GET-only for reads
    
    @mcpo_app.post(
        "/post_env",
        summary="Update Environment Variables",
        description=(
            "Write only the provided keys to .env. Secrets are opaque; do not echo values."
        ),
        operation_id="mcpo.post_env",
        openapi_extra={
            "x-instructions": [
                "Only set the keys provided by the user.",
                "Do not modify or delete other keys.",
                "Never echo or log secret values.",
                "Treat values as opaque strings."
            ]
        },
    )
    async def post_env(request: Request, body: PostEnvBody):
        """Update .env file with environment variables."""
        if _internal_read_only():
            return _read_only_error()
        try:
            env_vars = body.env_vars
            if not env_vars or not isinstance(env_vars, dict):
                logger.warning("post_env: Missing or invalid env_vars parameter")
                return JSONResponse(status_code=422, content={"ok": False, "error": {"message": "Missing or invalid env_vars (must be dict)"}})
            
            logger.info(f"post_env: Updating {len(env_vars)} environment variables: {list(env_vars.keys())}")
            
            # Read existing .env file
            env_path = ".env"
            existing_vars = {}
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            existing_vars[key] = value
            
            # Update with new variables
            existing_vars.update(env_vars)
            
            # Write back to .env file
            with open(env_path, 'w') as f:
                for key, value in existing_vars.items():
                    f.write(f"{key}={value}\n")
            
            # Set in current environment
            for key, value in env_vars.items():
                os.environ[key] = str(value)
            
            logger.info(f"post_env: Successfully updated environment variables: {list(env_vars.keys())}")
            return {"ok": True, "message": f"Updated {len(env_vars)} environment variables", "keys": list(env_vars.keys())}
            
        except Exception as e:
            logger.error(f"post_env: Failed to update environment variables: {str(e)}")
            return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Failed to update env: {str(e)}"}})
    
    # setup_server endpoint removed per design: enforce granular operations only
    
    @mcpo_app.post(
        "/validate_and_install",
        summary="Validate Config and Install Dependencies",
        description=(
            "Validate current configuration and, if requirements.txt exists, install dependencies."
        ),
        operation_id="mcpo.validate_and_install",
        openapi_extra={
            "x-instructions": [
                "Use for validation and dependency setup only when explicitly requested.",
                "Do not alter configuration content beyond validation scope."
            ]
        },
    )
    async def validate_and_install(request: Request, form_data: Dict[str, Any]):
        """Validate configuration and install dependencies."""
        if _internal_read_only():
            return _read_only_error()
        try:
            logger.info("validate_and_install: Starting validation and dependency installation")
            
            results = {
                "validation": {"status": "unknown", "errors": []},
                "installation": {"status": "unknown", "packages": []},
                "success": True
            }
            
            # Step 1: Validate current configuration
            try:
                main_app = mcpo_app.state.main_app
                config_path = getattr(main_app.state, 'config_path', None)
                
                if config_path and os.path.exists(config_path):
                    with open(config_path, 'r') as f:
                        config_data = json.load(f)  # This will raise JSONDecodeError if invalid
                    
                    # Basic validation
                    if "mcpServers" not in config_data:
                        results["validation"]["errors"].append("Missing 'mcpServers' section")
                        results["success"] = False
                    else:
                        server_count = len(config_data["mcpServers"])
                        results["validation"]["status"] = "valid"
                        results["validation"]["details"] = f"Configuration valid with {server_count} servers"
                        logger.info(f"validate_and_install: Configuration valid with {server_count} servers")
                else:
                    results["validation"]["errors"].append("No configuration file found")
                    results["success"] = False
                    
            except json.JSONDecodeError as e:
                results["validation"]["status"] = "invalid"
                results["validation"]["errors"].append(f"Invalid JSON: {str(e)}")
                results["success"] = False
                logger.error(f"validate_and_install: JSON validation failed - {str(e)}")
            except Exception as e:
                results["validation"]["status"] = "error"
                results["validation"]["errors"].append(f"Validation error: {str(e)}")
                results["success"] = False
                logger.error(f"validate_and_install: Validation error - {str(e)}")
            
            # Step 2: Install dependencies from requirements.txt if it exists
            if os.path.exists("requirements.txt"):
                try:
                    logger.info("validate_and_install: Installing dependencies from requirements.txt")
                    with open("requirements.txt", 'r') as f:
                        requirements = f.read().strip()
                    
                    if requirements:
                        result = subprocess.run(
                            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                            capture_output=True, text=True, timeout=300
                        )
                        
                        if result.returncode == 0:
                            results["installation"]["status"] = "success"
                            results["installation"]["details"] = "Dependencies installed successfully"
                            logger.info("validate_and_install: Dependencies installed successfully")
                        else:
                            results["installation"]["status"] = "failed"
                            results["installation"]["error"] = result.stderr
                            results["success"] = False
                            logger.error(f"validate_and_install: Dependency installation failed - {result.stderr}")
                    else:
                        results["installation"]["status"] = "skipped"
                        results["installation"]["details"] = "No dependencies to install"
                        
                except Exception as e:
                    results["installation"]["status"] = "error"
                    results["installation"]["error"] = str(e)
                    results["success"] = False
                    logger.error(f"validate_and_install: Installation error - {str(e)}")
            else:
                results["installation"]["status"] = "skipped"
                results["installation"]["details"] = "No requirements.txt found"
            
            if results["success"]:
                logger.info("validate_and_install: Validation and installation completed successfully")
            else:
                logger.warning("validate_and_install: Validation and installation completed with errors")
            
            return results
            
        except Exception as e:
            logger.error(f"validate_and_install: Critical failure - {str(e)}")
            return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Validation failed: {str(e)}"}})

    @mcpo_app.get(
        "/get_requirements",
        summary="Get requirements.txt contents",
        description="Read-only view of requirements.txt.",
        operation_id="mcpo.get_requirements",
        openapi_extra={
            "x-instructions": [
                "Do not make changes via this endpoint.",
                "Use POST /mcpo/post_requirements for edits."
            ]
        },
    )
    async def get_requirements_get(request: Request):
        try:
            if os.path.exists("requirements.txt"):
                with open("requirements.txt", "r", encoding="utf-8") as f:
                    content = f.read()
                return {"ok": True, "content": content}
            # create a sensible default template when missing
            return {"ok": True, "content": "# Python packages for MCP servers\n"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Failed to read requirements: {str(e)}"}})

    # legacy POST /get_requirements removed to enforce GET-only for reads

    @mcpo_app.post(
        "/post_requirements",
        summary="Write requirements.txt and install",
        description=(
            "Write the provided content verbatim to requirements.txt, install with pip -r, and attempt a hot reload. \n\n"
            "Rules:\n"
            "- Add or remove only what was explicitly requested.\n"
            "- Do not upgrade unrelated packages or add flags.\n"
            "- Keep other lines unchanged."
        ),
        operation_id="mcpo.post_requirements",
        openapi_extra={
            "x-instructions": [
                "Edit exactly as requested; preserve unrelated lines.",
                "Do not add upgrade flags or extras.",
                "On install error, report and stop."
            ]
        },
    )
    async def post_requirements(request: Request, body: PostRequirementsBody):
        if _internal_read_only():
            return _read_only_error()
        try:
            content = body.content
            if content is None or not isinstance(content, str):
                return JSONResponse(status_code=422, content={"ok": False, "error": {"message": "Missing or invalid 'content'"}})

            # Write requirements.txt
            try:
                with open("requirements.txt", "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Failed to write requirements.txt: {str(e)}"}})

            # Parse packages for response
            packages = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.strip().startswith('#')
            ]

            # Install with pip
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if result.returncode != 0:
                    logger.error("pip install failed: %s", result.stderr)
                    return JSONResponse(status_code=500, content={"ok": False, "error": {"message": "Dependency installation failed", "details": result.stderr}})
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=408, content={"ok": False, "error": {"message": "Dependency installation timed out"}})

            # Attempt hot reload if config is present
            try:
                main = mcpo_app.state.main_app
                cfg_path = getattr(main.state, 'config_path', None)
                if cfg_path and os.path.exists(cfg_path):
                    new_config = load_config(cfg_path)
                    await reload_config_handler(main, new_config)
                    main.state.aggregate_openapi_dirty = True
            except Exception as e:
                logger.warning("Reload after requirements install failed: %s", e)

            return {"ok": True, "message": "Requirements installed and servers reloaded", "packages": packages}
        except Exception as e:
            return JSONResponse(status_code=500, content={"ok": False, "error": {"message": f"Failed to process requirements: {str(e)}"}})
    
    return mcpo_app


async def build_main_app(
    host: str = "127.0.0.1",
    port: int = 8000,
    api_key: Optional[str] = "",
    cors_allow_origins=["*"],
    **kwargs,
) -> FastAPI:
    """Build the main FastAPI application without running it."""
    hot_reload = kwargs.get("hot_reload", False)
    # Server API Key
    api_dependency = get_verify_api_key(api_key) if api_key else None
    connection_timeout = kwargs.get("connection_timeout", None)
    strict_auth = kwargs.get("strict_auth", False)
    tool_timeout = int(kwargs.get("tool_timeout", 30))
    tool_timeout_max = int(kwargs.get("tool_timeout_max", 600))
    structured_output = kwargs.get("structured_output", False)
    read_only_mode = kwargs.get("read_only", False)
    protocol_version_mode = kwargs.get("protocol_version_mode", "warn")  # off|warn|enforce
    validate_output_mode = kwargs.get("validate_output_mode", "off")  # off|warn|enforce
    supported_protocol_versions = kwargs.get("supported_protocol_versions") or [MCP_VERSION]

    # MCP Server
    server_type = normalize_server_type(kwargs.get("server_type"))
    server_command = kwargs.get("server_command")

    # MCP Config
    config_path = kwargs.get("config_path")

    # mcpo server
    name = kwargs.get("name") or "MCP OpenAPI Proxy"
    description = (
        kwargs.get("description") or "Automatically generated API from MCP Tool Schemas"
    )
    version = kwargs.get("version") or "1.0"

    ssl_certfile = kwargs.get("ssl_certfile")
    ssl_keyfile = kwargs.get("ssl_keyfile")
    path_prefix = kwargs.get("path_prefix") or "/"
    log_level = kwargs.get("log_level", "info").upper()

    # Configure logging based on LOG_LEVEL environment variable
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    try:
        numeric_level = getattr(logging, log_level, None)
        if not isinstance(numeric_level, int):
            raise ValueError(f"Invalid log level: {log_level}")
    except (ValueError, AttributeError):
        logger.warning(f"Invalid LOG_LEVEL '{log_level}', defaulting to INFO")
        numeric_level = logging.INFO

    logging.basicConfig(
        level=numeric_level, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    # Add log buffer handler for UI
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))
    if not any(isinstance(handler, BufferedLogHandler) for handler in root_logger.handlers):
        root_logger.addHandler(
            BufferedLogHandler(
                _log_buffer,
                _log_buffer_lock,
                default_source="openapi",
                max_entries=MAX_LOG_ENTRIES,
            )
        )

    # Persistent rotating file log (survives restarts, unlike the UI buffer)
    setup_file_logging("serve", port)

    # Ensure access logs are retained for troubleshooting; rely on logging config for verbosity
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.INFO)
    logger.info("Starting MCPO Server...")
    logger.info(f"  Name: {name}")
    logger.info(f"  Version: {version}")
    logger.info(f"  Description: {description}")
    logger.info(f"  Hostname: {socket.gethostname()}")
    logger.info(f"  Port: {port}")
    logger.info(f"  API Key: {'Provided' if api_key else 'Not Provided'}")
    logger.info(f"  CORS Allowed Origins: {cors_allow_origins}")
    if ssl_certfile:
        logger.info(f"  SSL Certificate File: {ssl_certfile}")
    if ssl_keyfile:
        logger.info(f"  SSL Key File: {ssl_keyfile}")
    logger.info(f"  Path Prefix: {path_prefix}")

    # Create shutdown handler
    shutdown_handler = GracefulShutdown()

    main_app = FastAPI(
        title=name,
        description=description,
        version=version,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        lifespan=lifespan,
        openapi_version="3.1.0",
        dependencies=[Depends(api_dependency)] if api_dependency else [],
    )
    # Mount admin + meta routers for /_meta endpoints. Both share the /_meta
    # prefix; admin owns /env, /skills*, /code-mode while meta owns the larger
    # management surface (/servers, /config, /logs, /metrics, /reload, etc.).
    try:
        main_app.include_router(admin_router, prefix="/_meta")
    except Exception:
        logger.exception("Failed to include admin router")
    try:
        from mcpo.api.routers.meta import router as meta_router
        main_app.include_router(meta_router, prefix="/_meta")
    except Exception:
        logger.exception("Failed to include meta router")
    try:
        main_app.include_router(chat_router, prefix="/chat")
    except Exception:
        logger.exception("Failed to include chat router")
    try:
        main_app.include_router(providers_router, prefix="/chat")
    except Exception:
        logger.exception("Failed to include providers router")
    try:
        main_app.include_router(model_api_keys_router, prefix="/chat")
    except Exception:
        logger.exception("Failed to include model API key router")
    try:
        main_app.include_router(completions_router, prefix="")
    except Exception:
        logger.exception("Failed to include completions router")
    # Initialize shared StateManager
    state_manager = get_state_manager()
    main_app.state.state_manager = state_manager
    # Metrics counters (simple in-memory; reset on restart)
    main_app.state.metrics = {
        "tool_calls_total": 0,
        "tool_errors_total": 0,
        "tool_errors_by_code": {},  # code -> count
        "per_tool": {},  # tool_name -> stats dict
    }
    main_app.state.config_path = config_path  # Store for state persistence
    main_app.state.api_key = api_key
    main_app.state.model_api_key_store = get_model_api_key_store()
    main_app.state.read_only_mode = read_only_mode
    main_app.state.protocol_version_mode = protocol_version_mode
    main_app.state.validate_output_mode = validate_output_mode
    main_app.state.supported_protocol_versions = supported_protocol_versions
    proxy_url = kwargs.get("mcp_proxy_url")
    if not proxy_url:
        proxy_host = host if host and host not in {"0.0.0.0", "::", "0"} else "127.0.0.1"
        proxy_port = kwargs.get("mcp_proxy_port") or (port + 1 if port else 8001)
        proxy_url = f"http://{proxy_host}:{proxy_port}"
    main_app.state.mcp_proxy_url = proxy_url

    # Standardized error handlers
    @main_app.exception_handler(StateSaveError)
    async def state_save_exception_handler(request: Request, exc: StateSaveError):  # type: ignore
        logger.error("Control state persistence failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "Failed to persist control state",
                data=str(exc),
                code="state_save_failed",
            ),
        )

    @main_app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):  # type: ignore
        logger.error(f"Unhandled exception: {exc}")
        return JSONResponse(status_code=500, content=error_envelope("Internal Server Error"))

    # Convert FastAPI HTTPExceptions produced elsewhere into unified envelope
    from fastapi import HTTPException as _HTTPException  # type: ignore

    @main_app.exception_handler(_HTTPException)
    async def http_exception_handler(request: Request, exc: _HTTPException):  # type: ignore
        # exc.detail may be dict or str
        if isinstance(exc.detail, dict):
            message = exc.detail.get("message") or exc.detail.get("detail") or "HTTP Error"
            data = exc.detail.get("data") or {k: v for k, v in exc.detail.items() if k not in {"message", "detail"}}
        else:
            message = str(exc.detail)
            data = None
        return JSONResponse(status_code=exc.status_code, content=error_envelope(message, data=data))

    from fastapi.exceptions import RequestValidationError as _RVE  # local import

    @main_app.exception_handler(_RVE)
    async def validation_exception_handler(request: Request, exc: _RVE):  # type: ignore
        return JSONResponse(status_code=422, content=error_envelope("Validation Error", data=exc.errors()))

    # Pass shutdown handler to app state
    main_app.state.shutdown_handler = shutdown_handler
    main_app.state.path_prefix = path_prefix

    main_app.add_middleware(PackageArchiveBodyLimitMiddleware)
    main_app.add_middleware(ConfigTransactionMiddleware)
    main_app.add_middleware(ChatBodyLimitMiddleware)
    main_app.add_middleware(ModelAPIKeyMiddleware, api_key=api_key)

    # Store tool timeout in app state for handler usage
    main_app.state.tool_timeout = tool_timeout
    main_app.state.tool_timeout_max = tool_timeout_max
    main_app.state.structured_output = structured_output

    # Add middleware to protect also documentation and spec
    if api_key and strict_auth:
        main_app.add_middleware(APIKeyMiddleware, api_key=api_key)

    # CORS is added LAST so it is the OUTERMOST middleware; its headers then reach
    # auth error responses (401/403/429) from the auth middlewares above, which
    # short-circuit before inner middleware. Otherwise browser-based OpenAI
    # clients see an opaque CORS failure instead of a readable status (audit MED-5).
    main_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins or ["*"],
        allow_credentials=cors_allow_origins is not None and cors_allow_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register health endpoint early
    _register_health_endpoint(main_app)

    # Startup security / hardening audit warnings
    if not api_key and not read_only_mode:
        logger.warning("Security: Server running without API key and not in read-only mode – management endpoints are writable.")
    # /v1 inference is open when there is no admin key AND model-key enforcement
    # is off (no key has been created and lockdown was never enabled). Surface it
    # loudly with the exact remediation instead of failing silently open.
    if not api_key:
        try:
            _mk_store = getattr(main_app.state, "model_api_key_store", None) or get_model_api_key_store()
            if not _mk_store.enforcement_enabled():
                logger.warning(
                    "Security: /v1 inference endpoints are UNAUTHENTICATED "
                    "(no admin key, model-key enforcement off). Set MCPO_API_KEY "
                    "and restart to protect them. (The key-management endpoints "
                    "themselves require MCPO_API_KEY, so they cannot lock this "
                    "down while it is unset.)"
                )
        except ModelAPIKeyStoreError as exc:
            logger.warning("Security: could not read model API key store: %s", exc)
    if protocol_version_mode != "enforce":
        logger.info(f"Protocol negotiation not enforced (mode={protocol_version_mode}).")
    if validate_output_mode != "enforce":
        logger.info(f"Output schema validation not enforced (mode={validate_output_mode}).")

    @main_app.get("/chat/tools")
    async def list_chat_tools(server: Optional[str] = None):
        state_manager = main_app.state.state_manager
        if server:
            server_names = [server]
        else:
            server_names = [
                name
                for name, state in state_manager.get_all_states().items()
                if state.get("enabled", True)
            ]

        tools: Dict[str, List[str]] = {}
        for route in main_app.router.routes:
            if isinstance(route, Mount) and isinstance(route.app, FastAPI):
                mount_name = _server_name_from_mount(route)
                if mount_name not in server_names:
                    continue
                sub_app = route.app
                collected: List[str] = []
                for r in sub_app.router.routes:
                    methods = getattr(r, 'methods', set())
                    path = getattr(r, 'path', '')
                    if 'POST' in methods and path.count('/') == 1 and path not in ('/docs', '/openapi.json'):
                        collected.append(path.lstrip('/'))
                tools[mount_name] = sorted(collected)

        return {"ok": True, "tools": tools}

    # Create internal MCPO MCP server for self-management tools (mounted under /mcpo)
    mcpo_app = await create_internal_mcpo_server(main_app, api_dependency)
    main_app.mount("/mcpo", mcpo_app, name="mcpo")

    project_root = Path(__file__).resolve().parents[2]
    packaged_static_ui_dir = Path(__file__).resolve().parent / "static" / "ui"
    project_static_ui_dir = project_root / "static" / "ui"
    static_ui_dir = (
        packaged_static_ui_dir
        if packaged_static_ui_dir.is_dir()
        else project_static_ui_dir
    )
    mcp_settings_dist_dir = project_root / "mcp-server-settings" / "dist"
    mcp_settings_dir = project_root / "mcp-server-settings"

    # Mount static UI if available (served at /ui)
    try:
        if static_ui_dir.is_dir():
            main_app.mount("/ui", StaticFiles(directory=str(static_ui_dir), html=True), name="ui")
        else:
            logger.warning("UI directory not found at %s; /ui will not be mounted", static_ui_dir)
    except Exception:
        logger.warning("Failed mounting /ui static files", exc_info=True)
    # Provide primary access path /mcp; prefer settings app if present
    try:
        # If a built dist exists (e.g., Vite), serve that, else raw source folder with index.html
        if mcp_settings_dist_dir.is_dir():
            main_app.mount("/mcp", StaticFiles(directory=str(mcp_settings_dist_dir), html=True), name="mcp")
        elif (mcp_settings_dir / "index.html").is_file():
            main_app.mount("/mcp", StaticFiles(directory=str(mcp_settings_dir), html=True), name="mcp")
        else:
            # Fallback to minimal UI
            main_app.mount("/mcp", StaticFiles(directory=str(static_ui_dir), html=True), name="mcp")
    except Exception:
        logger.warning("Failed mounting /mcp static files", exc_info=True)

    headers = kwargs.get("headers")
    if headers and isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON format for headers. Headers will be ignored.")
            headers = None

    protocol_version_header = {"MCP-Protocol-Version": MCP_VERSION}
    if server_type == "sse":
        logger.info(
            f"Configuring for a single SSE MCP Server with URL {server_command[0]}"
        )
        main_app.state.server_type = "sse"
        main_app.state.args = server_command[0]  # Expects URL as the first element
        main_app.state.api_dependency = api_dependency
        merged = dict(headers) if headers else {}
        merged["MCP-Protocol-Version"] = MCP_VERSION
        main_app.state.headers = merged
    elif server_type == "streamable-http":
        logger.info(
            f"Configuring for a single StreamableHTTP MCP Server with URL {server_command[0]}"
        )
        main_app.state.server_type = "streamable-http"
        main_app.state.args = server_command[0]  # Expects URL as the first element
        main_app.state.api_dependency = api_dependency
        merged = dict(headers) if headers else {}
        merged["MCP-Protocol-Version"] = MCP_VERSION
        main_app.state.headers = merged
    elif server_command:  # This handles stdio
        logger.info(
            f"Configuring for a single Stdio MCP Server with command: {' '.join(server_command)}"
        )
        main_app.state.server_type = "stdio"  # Explicitly set type
        main_app.state.command = server_command[0]
        main_app.state.args = server_command[1:]
        main_app.state.env = os.environ.copy()
        main_app.state.api_dependency = api_dependency
    elif config_path:
        logger.info(f"Loading MCP server configurations from: {config_path}")
        config_data = load_config(config_path)
        mount_config_servers(
            main_app, config_data, cors_allow_origins, api_key, strict_auth,
            api_dependency, connection_timeout, lifespan, path_prefix
        )

        # Store config info and app state for hot reload
        main_app.state.config_path = config_path
        main_app.state.config_data = config_data
        main_app.state.cors_allow_origins = cors_allow_origins
        main_app.state.api_key = api_key
        main_app.state.strict_auth = strict_auth
        main_app.state.api_dependency = api_dependency
        main_app.state.connection_timeout = connection_timeout
        main_app.state.lifespan = lifespan
        main_app.state.path_prefix = path_prefix
        main_app.state.tool_timeout = tool_timeout
        main_app.state.ssl_certfile = ssl_certfile
        main_app.state.ssl_keyfile = ssl_keyfile
    else:
        # Allow running without server configuration for testing/minimal mode
        logger.info("Running in minimal mode without MCP server configuration.")

    # Store SSL config in app state regardless of config source
    main_app.state.ssl_certfile = ssl_certfile
    main_app.state.ssl_keyfile = ssl_keyfile

    # Setup hot reload if enabled and config_path is provided
    config_watcher = None
    if hot_reload and config_path:
        logger.info(f"Enabling hot reload for config file: {config_path}")

        async def reload_callback(_new_config):
            async with _get_config_transaction_lock(main_app):
                current_config = load_config(config_path)
                await _reload_runtime_surfaces_with_rollback(
                    main_app,
                    current_config,
                )

        config_watcher = ConfigWatcher(config_path, reload_callback)
        config_watcher.start()
        main_app.state.config_watcher = config_watcher

    return main_app


async def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    api_key: Optional[str] = "",
    cors_allow_origins=["*"],
    **kwargs,
):
    """Build and run the FastAPI application."""
    main_app = await build_main_app(
        host=host,
        port=port,
        api_key=api_key,
        cors_allow_origins=cors_allow_origins,
        **kwargs,
    )

    # Get SSL config from kwargs and app state
    ssl_certfile = kwargs.get("ssl_certfile") or getattr(main_app.state, "ssl_certfile", None)
    ssl_keyfile = kwargs.get("ssl_keyfile") or getattr(main_app.state, "ssl_keyfile", None)
    
    # Get shutdown handler from app state
    shutdown_handler = getattr(main_app.state, "shutdown_handler", None)
    if not shutdown_handler:
        shutdown_handler = GracefulShutdown()

    # Get hot reload config
    hot_reload = kwargs.get("hot_reload", False)
    config_path = kwargs.get("config_path")
    config_watcher = getattr(main_app.state, "config_watcher", None)
    if hot_reload and config_path and config_watcher is None:
        logger.info(f"Enabling hot reload for config file: {config_path}")

        async def reload_callback(_new_config):
            async with _get_config_transaction_lock(main_app):
                current_config = load_config(config_path)
                await _reload_runtime_surfaces_with_rollback(
                    main_app,
                    current_config,
                )

        config_watcher = ConfigWatcher(config_path, reload_callback)
        config_watcher.start()
        main_app.state.config_watcher = config_watcher

    logger.info("Uvicorn server starting...")
    uvicorn_log_level = logging.getLevelName(numeric_level).lower()
    config = uvicorn.Config(
        app=main_app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_level=uvicorn_log_level,
    )
    # uvicorn.Config just applied its dictConfig, which strips handlers from the
    # non-propagating uvicorn loggers; re-attach the file handler so access and
    # error lines persist to disk.
    reattach_uvicorn_file_handlers()
    server = uvicorn.Server(config)

    # Setup signal handlers
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, lambda s=sig: shutdown_handler.handle_signal(s)
            )
    except NotImplementedError:
        logger.warning(
            "loop.add_signal_handler is not available on this platform. Using signal.signal()."
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda s, f: shutdown_handler.handle_signal(s))

    # Modified server startup
    try:
        # Create server task
        server_task = asyncio.create_task(server.serve())
        shutdown_handler.track_task(server_task)

        # Wait for either the server to fail or a shutdown signal
        shutdown_wait_task = asyncio.create_task(shutdown_handler.shutdown_event.wait())
        done, pending = await asyncio.wait(
            [server_task, shutdown_wait_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if server_task in done:
            # Check if the server task raised an exception
            try:
                server_task.result()  # This will raise the exception if there was one
                logger.warning("Server task exited unexpectedly. Initiating shutdown.")
            except SystemExit as e:
                logger.error(f"Server failed to start: {e}")
                raise  # Re-raise SystemExit to maintain proper exit behavior
            except Exception as e:
                logger.error(f"Server task failed with exception: {e}")
                raise
            shutdown_handler.shutdown_event.set()

        # Cancel the other task
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Graceful shutdown if server didn't fail with SystemExit
        logger.info("Initiating server shutdown...")
        server.should_exit = True

        # Cancel all tracked tasks
        for task in list(shutdown_handler.tasks):
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete
        if shutdown_handler.tasks:
            await asyncio.gather(*shutdown_handler.tasks, return_exceptions=True)

    except SystemExit:
        # Re-raise SystemExit to allow proper program termination
        logger.info("Server startup failed, exiting...")
        raise
    except Exception as e:
        logger.error(f"Error during server execution: {e}")
        raise
    finally:
        # Stop config watcher if it was started
        if config_watcher:
            config_watcher.stop()
        logger.info("Server shutdown complete")
