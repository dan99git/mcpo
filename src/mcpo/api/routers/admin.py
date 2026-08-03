"""
Admin router for server management and configuration operations.
"""
import asyncio
import logging
import re
from typing import Dict, Any
from fastapi import FastAPI, APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount
import os
from pydantic import BaseModel, Field

from mcpo.services.state import get_state_manager
from mcpo.services.skills import (
    compile_skills_system_prompt,
    delete_skill_file,
    get_skill,
    scan_skills,
    upsert_skill_file,
)
from mcpo.services.skill_packages import (
    MAX_ARCHIVE_BASE64_CHARS,
    SkillPackageConflict,
    SkillPackageError,
    SkillPackageNotFound,
    inspect_skill_archive,
    install_skill_archive,
    list_skill_packages,
    uninstall_skill_package,
)
from mcpo.services.cli_packages import (
    CliPackageCollisionError,
    CliPackageError,
    CliPackageExecutableNotFoundError,
    CliPackageInstallError,
    CliPackageManifestError,
    CliPackageNotFoundError,
    CliPackageSafetyError,
    CliPackageValidationError,
    install_cli_package,
    list_cli_packages,
    plan_cli_package,
    uninstall_cli_package,
)
from mcpo.services.tool_manifests import (
    ToolManifestArchiveError,
    preview_tool_manifest_archive,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _admin_error_envelope(
    message: str,
    code: str = "error",
    details: Dict[str, Any] | None = None,
) -> dict:
    error: Dict[str, Any] = {"message": message, "code": code}
    if details is not None:
        error["details"] = details
    return {"ok": False, "error": error}


class SkillUpsertRequest(BaseModel):
    id: str = Field(..., description="Stable skill ID")
    title: str = Field(..., description="Display name")
    description: str = Field("", description="Short description")
    content: str = Field(..., description="Skill markdown body")


class SkillArchiveRequest(BaseModel):
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original .skill or .zip filename",
    )
    content_base64: str = Field(
        ...,
        min_length=1,
        max_length=MAX_ARCHIVE_BASE64_CHARS,
        description="Base64 encoded archive bytes",
    )
    confirmed: bool = Field(False, description="Explicitly confirm installation")


class ToolManifestPreviewRequest(BaseModel):
    filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original .skill or .zip filename",
    )
    content_base64: str = Field(
        ...,
        min_length=1,
        max_length=MAX_ARCHIVE_BASE64_CHARS,
        description="Base64 encoded archive bytes",
    )


class PackageConfirmationRequest(BaseModel):
    confirmed: bool = Field(False, description="Explicitly confirm package removal")


class CliPackageRequest(BaseModel):
    kind: str = Field(..., description="Package manager: python or npm")
    spec: str = Field(..., description="Exact pinned package spec")
    allow_scripts: bool = Field(False, description="Allow npm lifecycle scripts")
    confirmed: bool = Field(False, description="Explicitly confirm installation")


def _cli_package_error_response(exc: CliPackageError) -> JSONResponse:
    message = str(exc)
    if isinstance(exc, CliPackageValidationError):
        status_code, code = 422, "invalid_cli_package"
    elif isinstance(exc, CliPackageNotFoundError):
        status_code, code = 404, "not_found"
    elif isinstance(exc, CliPackageCollisionError):
        status_code, code = 409, "cli_package_conflict"
    elif isinstance(exc, CliPackageExecutableNotFoundError):
        status_code, code = 503, "installer_unavailable"
    elif isinstance(exc, (CliPackageManifestError, CliPackageSafetyError)):
        status_code, code = 409, "invalid_cli_package_state"
        message = "CLI package state is invalid"
        logger.warning("CLI package state validation failed: %s", exc)
    elif isinstance(exc, CliPackageInstallError):
        status_code, code = 502, "cli_install_failed"
    else:
        status_code, code = 500, "cli_package_error"
    details = None
    if isinstance(exc, CliPackageInstallError):
        details = {
            "argv": exc.argv,
            "returnCode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    return JSONResponse(
        status_code=status_code,
        content=_admin_error_envelope(message, code=code, details=details),
    )

# Initialize global variable first
_FASTMCP_AVAILABLE = None

class _TaskOwnedLifespan:
    """Normal-close handle whose inner lifespan is owned by one dedicated task."""

    def __init__(self, context_manager: Any, *, label: str) -> None:
        self._context_manager = context_manager
        self._label = label
        self._ready: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._startup_error: BaseException | None = None
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        assert self._ready is not None
        assert self._stop is not None
        try:
            await self._context_manager.__aenter__()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            await self._stop.wait()
        finally:
            await self._context_manager.__aexit__(None, None, None)

    async def __aenter__(self) -> "_TaskOwnedLifespan":
        if self._task is not None:
            raise RuntimeError(f"FastMCP lifespan '{self._label}' is already started")

        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(),
            name=f"mcpo-fastmcp-lifespan:{self._label}",
        )
        try:
            await self._ready.wait()
            if self._startup_error is not None:
                await self._task
                raise self._startup_error
            if self._task.done():
                await self._task
        except BaseException:
            if not self._task.done():
                self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
            raise
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if self._task is None or self._stop is None:
            return False
        self._stop.set()
        await self._task
        return False


def _get_fastmcp_remount_lock(main_app: FastAPI) -> asyncio.Lock:
    """Return the app-wide lock that serializes native proxy generation swaps."""
    lock = getattr(main_app.state, "fastmcp_remount_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        main_app.state.fastmcp_remount_lock = lock
    return lock


def _record_fastmcp_teardown_failure(
    main_app: FastAPI,
    *,
    label: str,
    lifespan: Any,
    error: BaseException,
) -> None:
    """Retain a failed cleanup handle so degradation is never silent or discarded."""
    failures = dict(
        getattr(main_app.state, "fastmcp_proxy_teardown_failures", {}) or {}
    )
    failures[f"{label}#{id(lifespan)}"] = {
        "lifespan": lifespan,
        "error": f"{type(error).__name__}: {error}",
    }
    main_app.state.fastmcp_proxy_teardown_failures = failures


async def _mount_or_remount_fastmcp(
    main_app: FastAPI,
    base_path: str = "/mcp",
) -> None:
    """Atomically replace native FastMCP mounts while preserving lifespan ownership."""
    async with _get_fastmcp_remount_lock(main_app):
        if getattr(main_app.state, "fastmcp_remount_closed", False):
            raise RuntimeError("FastMCP proxy remount is closed during shutdown")
        await _mount_or_remount_fastmcp_unlocked(main_app, base_path=base_path)


async def _shutdown_fastmcp_proxy(main_app: FastAPI) -> None:
    """Serialize shutdown with remounts and prevent a watcher from republishing routes."""
    async with _get_fastmcp_remount_lock(main_app):
        main_app.state.fastmcp_remount_closed = True
        active_lifespans = dict(
            getattr(main_app.state, "fastmcp_proxy_lifespans", {}) or {}
        )
        main_app.router.routes[:] = [
            route
            for route in main_app.router.routes
            if not bool(
                getattr(
                    getattr(getattr(route, "app", None), "state", None),
                    "is_fastmcp_proxy",
                    False,
                )
            )
        ]
        main_app.state.fastmcp_proxy_mounts = []
        main_app.state.fastmcp_proxy_global_mount = None
        main_app.state.fastmcp_proxy_lifespans = {}

        for key, lifespan in reversed(list(active_lifespans.items())):
            try:
                await lifespan.__aexit__(None, None, None)
            except BaseException as exc:
                _record_fastmcp_teardown_failure(
                    main_app,
                    label=f"shutdown:{key}",
                    lifespan=lifespan,
                    error=exc,
                )
                logger.error(
                    "Error shutting down FastMCP proxy lifespan for '%s': %s",
                    key,
                    exc,
                    exc_info=True,
                )

async def _mount_or_remount_fastmcp_unlocked(
    main_app: FastAPI,
    *,
    base_path: str,
) -> None:
    """Build and start a full candidate generation before publishing it."""
    global _FASTMCP_AVAILABLE

    old_routes = [
        route
        for route in main_app.router.routes
        if bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
    ]
    old_lifespans = dict(
        getattr(main_app.state, "fastmcp_proxy_lifespans", {}) or {}
    )
    new_lifespans: Dict[str, Any] = {}
    candidate_routes: list[Mount] = []
    proxy_mounts: list[dict[str, Any]] = []

    def _compose_path(*segments: str) -> str:
        parts = []
        for segment in segments:
            if not segment:
                continue
            stripped = segment.strip("/")
            if stripped:
                parts.append(stripped)
        if not parts:
            return "/"
        return "/" + "/".join(parts)

    path_prefix = getattr(main_app.state, "path_prefix", "/") or "/"
    global_mount_path = _compose_path(path_prefix, base_path)
    candidate_global_mount: str | None = None

    try:
        config_data = getattr(main_app.state, "config_data", None)
        if not isinstance(config_data, dict):
            raise RuntimeError("No valid config_data is available for proxy remount")

        from mcpo.utils.config import interpolate_env_placeholders_in_config

        raw_cfg = interpolate_env_placeholders_in_config(config_data)
        state_manager = get_state_manager()
        all_servers_cfg = (
            raw_cfg.get("mcpServers", {}) if isinstance(raw_cfg, dict) else {}
        )
        enabled_servers_cfg: Dict[str, Any] = {}
        for server_name, server_cfg in all_servers_cfg.items():
            if isinstance(server_cfg, dict) and not server_cfg.get("enabled", True):
                logger.info(
                    "Skipping FastMCP proxy mount for '%s' (disabled in config)",
                    server_name,
                )
                continue
            if not state_manager.is_server_enabled(server_name):
                logger.info(
                    "Skipping FastMCP proxy mount for '%s' (disabled in shared state)",
                    server_name,
                )
                continue
            enabled_servers_cfg[server_name] = server_cfg

        raw_cfg = dict(raw_cfg)
        raw_cfg["mcpServers"] = enabled_servers_cfg

        if enabled_servers_cfg:
            if _FASTMCP_AVAILABLE is None:
                try:
                    from fastmcp.server.server import FastMCP  # type: ignore # noqa: F401
                    from fastmcp.mcp_config import MCPConfig  # type: ignore # noqa: F401

                    _FASTMCP_AVAILABLE = True
                except Exception:
                    _FASTMCP_AVAILABLE = False

            if not _FASTMCP_AVAILABLE:
                raise RuntimeError("FastMCP is unavailable; proxy remount failed")

            from fastmcp.mcp_config import MCPConfig  # type: ignore
            from mcpo.proxy import (
                _add_mcp_host_origin_guard,
                _proxy_no_roots_forward,
            )

            cfg = MCPConfig.from_dict(raw_cfg)
            stateless_http = getattr(main_app.state, "proxy_stateless", None)
            proxy = _proxy_no_roots_forward(
                cfg,
                stateless=bool(stateless_http),
                server_name=None,
            )

            api_key = getattr(main_app.state, "api_key", None)
            strict_auth = getattr(main_app.state, "strict_auth", False)
            api_key_middleware_cls = None
            if api_key and strict_auth:
                from mcpo.utils.auth import APIKeyMiddleware

                api_key_middleware_cls = APIKeyMiddleware

            def _secure_app(app_to_secure):
                if api_key_middleware_cls is None:
                    return app_to_secure
                secure_wrapper = FastAPI()
                secure_wrapper.add_middleware(
                    api_key_middleware_cls,
                    api_key=api_key,
                )
                _add_mcp_host_origin_guard(secure_wrapper)
                secure_wrapper.mount("/", app_to_secure)
                setattr(secure_wrapper.state, "is_fastmcp_proxy", True)
                setattr(
                    secure_wrapper.state,
                    "proxy_scope",
                    getattr(app_to_secure.state, "proxy_scope", None),
                )
                if hasattr(app_to_secure.state, "proxy_server_name"):
                    setattr(
                        secure_wrapper.state,
                        "proxy_server_name",
                        getattr(app_to_secure.state, "proxy_server_name"),
                    )
                return secure_wrapper

            used_paths: set[str] = {global_mount_path}
            global_app = proxy.http_app(
                path="/",
                transport="streamable-http",
                stateless_http=stateless_http,
                host_origin_protection=True,
            )
            setattr(global_app.state, "is_fastmcp_proxy", True)
            setattr(global_app.state, "proxy_scope", "global")
            setattr(global_app.state, "proxy_mount_base", base_path)

            wrapped_global = _secure_app(global_app)
            global_lifespan = _TaskOwnedLifespan(
                global_app.router.lifespan_context(global_app),
                label=global_mount_path,
            )
            try:
                await global_lifespan.__aenter__()
            except Exception as exc:
                raise RuntimeError(
                    "Failed to start FastMCP proxy lifespan for aggregate mount"
                ) from exc
            new_lifespans[global_mount_path] = global_lifespan
            candidate_routes.append(Mount(global_mount_path, app=wrapped_global))
            candidate_global_mount = global_mount_path
            proxy_mounts.append(
                {
                    "id": "global" if global_mount_path != "/" else "root",
                    "label": "All MCPs",
                    "path": global_mount_path,
                    "scope": "global",
                }
            )

            for server_name, server_cfg in enabled_servers_cfg.items():
                mount_segment = str(server_name).strip().strip("/")
                if not mount_segment:
                    raise ValueError("MCP server names must not be blank")

                single_cfg = MCPConfig.from_dict(
                    {"mcpServers": {server_name: server_cfg}}
                )
                try:
                    single_proxy = _proxy_no_roots_forward(
                        single_cfg,
                        stateless=bool(stateless_http),
                        server_name=server_name,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to initialize FastMCP proxy for server '{server_name}'"
                    ) from exc

                sub_app = single_proxy.http_app(
                    path="/",
                    transport="streamable-http",
                    stateless_http=stateless_http,
                    host_origin_protection=True,
                )
                setattr(sub_app.state, "is_fastmcp_proxy", True)
                setattr(sub_app.state, "proxy_scope", "server")
                setattr(sub_app.state, "proxy_server_name", server_name)

                mount_path = _compose_path(
                    path_prefix,
                    base_path,
                    mount_segment,
                )
                if mount_path in used_paths:
                    raise RuntimeError(
                        f"MCP server '{server_name}' duplicates mount path {mount_path}"
                    )
                used_paths.add(mount_path)

                wrapped_sub_app = _secure_app(sub_app)
                sub_lifespan = _TaskOwnedLifespan(
                    sub_app.router.lifespan_context(sub_app),
                    label=mount_path,
                )
                try:
                    await sub_lifespan.__aenter__()
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to start FastMCP proxy lifespan for server '{server_name}'"
                    ) from exc
                new_lifespans[mount_path] = sub_lifespan
                candidate_routes.append(Mount(mount_path, app=wrapped_sub_app))

                label = server_name
                if isinstance(server_cfg, dict):
                    label = (
                        server_cfg.get("name")
                        or server_cfg.get("displayName")
                        or server_name
                    )
                proxy_mounts.append(
                    {
                        "id": mount_segment,
                        "label": label,
                        "path": mount_path,
                        "scope": "server",
                        "server": server_name,
                    }
                )
    except BaseException as exc:
        for key, lifespan in reversed(list(new_lifespans.items())):
            try:
                await lifespan.__aexit__(None, None, None)
            except BaseException as cleanup_exc:
                _record_fastmcp_teardown_failure(
                    main_app,
                    label=f"candidate:{key}",
                    lifespan=lifespan,
                    error=cleanup_exc,
                )
                logger.error(
                    "Failed to clean up candidate FastMCP proxy lifespan for '%s': %s",
                    key,
                    cleanup_exc,
                    exc_info=True,
                )
        logger.error("Failed to build FastMCP proxy candidate: %s", exc, exc_info=True)
        raise

    candidate_routes.sort(
        key=lambda route: len(getattr(route, "path", "")),
        reverse=True,
    )
    old_route_ids = {id(route) for route in old_routes}
    current_routes = list(main_app.router.routes)
    retained_routes = [
        route for route in current_routes if id(route) not in old_route_ids
    ]
    if old_route_ids:
        insertion_index = min(
            index
            for index, route in enumerate(current_routes)
            if id(route) in old_route_ids
        )
        insertion_index = min(insertion_index, len(retained_routes))
    else:
        insertion_index = next(
            (
                index
                for index, route in enumerate(retained_routes)
                if getattr(route, "path", None) == global_mount_path
            ),
            len(retained_routes),
        )

    main_app.router.routes[:] = (
        retained_routes[:insertion_index]
        + candidate_routes
        + retained_routes[insertion_index:]
    )
    main_app.state.fastmcp_proxy_mounts = proxy_mounts
    main_app.state.fastmcp_proxy_global_mount = candidate_global_mount
    main_app.state.fastmcp_proxy_lifespans = new_lifespans

    for key, lifespan in reversed(list(old_lifespans.items())):
        try:
            await lifespan.__aexit__(None, None, None)
        except BaseException as exc:
            _record_fastmcp_teardown_failure(
                main_app,
                label=f"retired:{key}",
                lifespan=lifespan,
                error=exc,
            )
            logger.error(
                "Error shutting down previous FastMCP proxy lifespan for '%s': %s",
                key,
                exc,
                exc_info=True,
            )

    if enabled_servers_cfg:
        logger.info(
            "Atomically published %d FastMCP proxy route(s) at %s",
            len(candidate_routes),
            global_mount_path,
        )
    else:
        logger.info("No enabled MCP servers remain; proxy mounts cleared")

def unmount_servers(main_app: FastAPI, path_prefix: str, server_names: list):
    """Unmount specific MCP servers."""
    for server_name in server_names:
        mount_path = f"{path_prefix}{server_name}"
        # Find and remove the mount
        routes_to_remove = []
        for route in main_app.router.routes:
            if hasattr(route, 'path') and route.path == mount_path:
                routes_to_remove.append(route)

        for route in routes_to_remove:
            main_app.router.routes.remove(route)
            logger.info(f"Unmounted server: {server_name}")


# Global variable already initialized above

@router.post("/env")
def update_env_vars(data: Dict[str, str], request: Request):
    """Update environment variables in local .env file. Minimal implementation.
    This endpoint is intended for local/dev use; secure or disable in production.
    """
    import re as _re
    # Block in read-only mode
    if getattr(request.app.state, 'read_only_mode', False):
        raise HTTPException(status_code=403, detail="Read-only mode")
    
    # Deny dangerous keys
    DENIED_KEYS = {"PATH", "HOME", "USER", "SHELL", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}
    
    try:
        env_path = ".env"
        existing: Dict[str, str] = {}
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    existing[k] = v
        # update
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            # Validate key format
            if not _re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k):
                raise HTTPException(status_code=422, detail=f"Invalid env var key: {k}")
            if k.upper() in DENIED_KEYS:
                raise HTTPException(status_code=403, detail=f"Cannot modify protected key: {k}")
            existing[k] = str(v)
            # also update process env for this process
            os.environ[k] = str(v)
        # write back
        with open(env_path, 'w', encoding='utf-8') as f:
            for k, v in existing.items():
                f.write(f"{k}={v}\n")
        return {"status": "updated", "count": len(data)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _skill_payload(skill, *, include_content: bool = True) -> Dict[str, Any]:
    payload = {
        "id": skill.id,
        "title": skill.title,
        "description": skill.description,
        "enabled": skill.enabled,
        "priority": skill.priority,
        "scopes": skill.scopes or [],
        "providers": skill.providers or [],
        "models": skill.models or [],
        "tags": skill.tags or [],
        "sourcePath": skill.source_path,
        "sourceKind": skill.source_kind,
        "packageId": skill.package_id,
        "format": skill.format,
        "editable": skill.editable,
        "resourceCount": skill.resource_count,
        "contentChars": len(skill.content or ""),
    }
    if include_content:
        payload["content"] = skill.content
    return payload


@router.get("/skills")
async def list_agent_skills():
    skills, issues = scan_skills()
    return {
        "ok": True,
        "skills": [_skill_payload(item, include_content=False) for item in skills],
        "issues": issues,
    }


@router.get("/skills/{skill_id}")
async def get_agent_skill(skill_id: str):
    skill = get_skill(skill_id)
    if not skill:
        return JSONResponse(
            status_code=404,
            content=_admin_error_envelope("Skill not found", code="not_found"),
        )
    return {
        "ok": True,
        "skill": _skill_payload(skill),
    }


@router.post("/skills")
async def upsert_agent_skill(payload: SkillUpsertRequest, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}},
        )
    try:
        skill = upsert_skill_file(
            skill_id=payload.id,
            title=payload.title,
            description=payload.description,
            content=payload.content,
        )
        return {
            "ok": True,
            "skill": _skill_payload(skill),
        }
    except PermissionError as exc:
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope(str(exc), code="package_read_only"),
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content=_admin_error_envelope(str(exc), code="invalid_skill"),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_admin_error_envelope(f"Failed to save skill: {exc}", code="save_failed"),
        )


@router.delete("/skills/{skill_id}")
async def delete_agent_skill(skill_id: str, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}},
        )
    try:
        deleted = delete_skill_file(skill_id)
    except PermissionError as exc:
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope(str(exc), code="package_read_only"),
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(str(exc), code="package_resources"),
        )
    if not deleted:
        return JSONResponse(
            status_code=404,
            content=_admin_error_envelope("Skill not found", code="not_found"),
        )
    return {"ok": True, "deleted": True}


@router.post("/skills/{skill_id}/enable")
async def enable_agent_skill(skill_id: str, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}},
        )
    skill = get_skill(skill_id)
    if not skill:
        return JSONResponse(
            status_code=404,
            content=_admin_error_envelope("Skill not found", code="not_found"),
        )
    state = get_state_manager()
    state.set_skill_enabled(skill.id, True)
    return {"ok": True, "enabled": True}


@router.post("/skills/{skill_id}/disable")
async def disable_agent_skill(skill_id: str, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}},
        )
    skill = get_skill(skill_id)
    if not skill:
        return JSONResponse(
            status_code=404,
            content=_admin_error_envelope("Skill not found", code="not_found"),
        )
    state = get_state_manager()
    state.set_skill_enabled(skill.id, False)
    return {"ok": True, "enabled": False}


@router.post("/skills/preview-prompt")
async def preview_skill_prompt(payload: Dict[str, Any]):
    scope = str(payload.get("scope") or "chat")
    model = payload.get("model")
    provider = payload.get("provider")
    skill_ids = payload.get("skill_ids")
    prompt = compile_skills_system_prompt(
        scope=scope,
        model=model,
        provider=provider,
        requested_skill_ids=skill_ids if isinstance(skill_ids, list) else None,
    )
    return {"ok": True, "prompt": prompt}


@router.get("/cli-packages")
async def get_cli_packages():
    try:
        packages = await run_in_threadpool(list_cli_packages)
        return {"ok": True, "packages": packages}
    except CliPackageError as exc:
        return _cli_package_error_response(exc)


@router.post("/cli-packages/plan")
async def plan_agent_cli_package(payload: CliPackageRequest):
    try:
        plan = plan_cli_package(
            payload.kind,
            payload.spec,
            allow_scripts=payload.allow_scripts,
        )
        return {"ok": True, "plan": plan}
    except CliPackageError as exc:
        return _cli_package_error_response(exc)


@router.post("/cli-packages/install")
async def install_agent_cli_package(payload: CliPackageRequest, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope("Read-only mode", code="read_only"),
        )
    if not payload.confirmed:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(
                "Explicit confirmation is required", code="confirmation_required"
            ),
        )
    try:
        package = await run_in_threadpool(
            install_cli_package,
            payload.kind,
            payload.spec,
            allow_scripts=payload.allow_scripts,
        )
        return {"ok": True, "package": package}
    except CliPackageError as exc:
        return _cli_package_error_response(exc)


@router.post("/cli-packages/{package_id}/uninstall")
async def uninstall_agent_cli_package(
    package_id: str,
    payload: PackageConfirmationRequest,
    request: Request,
):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope("Read-only mode", code="read_only"),
        )
    if not payload.confirmed:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(
                "Explicit confirmation is required", code="confirmation_required"
            ),
        )
    try:
        package = await run_in_threadpool(uninstall_cli_package, package_id)
        return {
            "ok": True,
            "id": package["packageId"],
            "removed": package["removed"],
        }
    except CliPackageError as exc:
        return _cli_package_error_response(exc)


@router.get("/skill-packages")
async def get_skill_packages():
    try:
        packages = await run_in_threadpool(list_skill_packages)
        return {"ok": True, "packages": packages}
    except SkillPackageError as exc:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(
                str(exc), code="invalid_skill_package_state"
            ),
        )


@router.post("/tool-manifests/preview")
async def preview_agent_tool_manifests(payload: ToolManifestPreviewRequest):
    try:
        preview = await run_in_threadpool(
            preview_tool_manifest_archive,
            payload.filename,
            payload.content_base64,
        )
        return {"ok": True, "preview": preview}
    except ToolManifestArchiveError as exc:
        return JSONResponse(
            status_code=422,
            content=_admin_error_envelope(
                str(exc),
                code="invalid_tool_archive",
            ),
        )
    except CliPackageError as exc:
        return _cli_package_error_response(exc)


@router.post("/skill-packages/inspect")
async def inspect_agent_skill_package(payload: SkillArchiveRequest):
    try:
        inspection = await run_in_threadpool(
            inspect_skill_archive,
            payload.filename,
            payload.content_base64,
        )
        return {"ok": True, "inspection": inspection}
    except SkillPackageError as exc:
        return JSONResponse(
            status_code=422,
            content=_admin_error_envelope(str(exc), code="invalid_skill_package"),
        )


@router.post("/skill-packages/install")
async def install_agent_skill_package(payload: SkillArchiveRequest, request: Request):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope("Read-only mode", code="read_only"),
        )
    if not payload.confirmed:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(
                "Explicit confirmation is required", code="confirmation_required"
            ),
        )
    try:
        package = await run_in_threadpool(
            install_skill_archive,
            payload.filename,
            payload.content_base64,
        )
        state = get_state_manager()
        for skill in package.get("skills", []):
            skill_id = skill.get("id") if isinstance(skill, dict) else None
            if skill_id:
                state.set_skill_enabled(str(skill_id), False)
        return {"ok": True, "package": package}
    except SkillPackageConflict as exc:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(str(exc), code="skill_package_conflict"),
        )
    except SkillPackageError as exc:
        return JSONResponse(
            status_code=422,
            content=_admin_error_envelope(str(exc), code="invalid_skill_package"),
        )


@router.post("/skill-packages/{package_id}/uninstall")
async def uninstall_agent_skill_package(
    package_id: str,
    payload: PackageConfirmationRequest,
    request: Request,
):
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content=_admin_error_envelope("Read-only mode", code="read_only"),
        )
    if not payload.confirmed:
        return JSONResponse(
            status_code=409,
            content=_admin_error_envelope(
                "Explicit confirmation is required", code="confirmation_required"
            ),
        )
    try:
        result = await run_in_threadpool(uninstall_skill_package, package_id)
        return {"ok": True, **result}
    except SkillPackageNotFound as exc:
        return JSONResponse(
            status_code=404,
            content=_admin_error_envelope(str(exc), code="not_found"),
        )
    except SkillPackageError as exc:
        return JSONResponse(
            status_code=422,
            content=_admin_error_envelope(str(exc), code="invalid_skill_package"),
        )


# --- Code Mode ---

@router.get("/code-mode")
async def get_code_mode():
    """Get current code mode state."""
    state = get_state_manager()
    return {"ok": True, "enabled": state.is_code_mode_enabled()}


class CodeModeRequest(BaseModel):
    enabled: bool = Field(..., description="Enable or disable code mode")


@router.post("/code-mode")
async def set_code_mode(payload: CodeModeRequest, request: Request):
    """Enable or disable code mode.

    When enabled, the MCP proxy exposes only two meta-tools (search_tools and
    execute_tool) instead of all individual tools. This reduces context usage
    for LLMs while still allowing access to the full tool catalog via search.
    """
    if getattr(request.app.state, "read_only_mode", False):
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": {"message": "Read-only mode", "code": "read_only"}},
        )
    state = get_state_manager()
    state.set_code_mode_enabled(payload.enabled)
    return {"ok": True, "enabled": state.is_code_mode_enabled()}


# --- Changelog ---

@router.get("/changelog")
async def get_changelog():
    """Serve CHANGELOG.md as raw markdown for the UI changelog page.

    Dev layout: <project root>/CHANGELOG.md (admin.py is src/mcpo/api/routers/,
    so parents[4] is the project root). Packaged fallback: alongside the mcpo
    package (parents[2]), mirroring how THIRD_PARTY_NOTICES.md is force-included.
    """
    from pathlib import Path

    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "CHANGELOG.md",  # dev checkout: src/mcpo/api/routers -> root
        here.parents[2] / "CHANGELOG.md",  # installed package fallback
    ]
    for path in candidates:
        try:
            if path.is_file():
                return {"ok": True, "content": path.read_text(encoding="utf-8")}
        except Exception as e:
            logger.warning("Failed reading changelog at %s: %s", path, e)
    return JSONResponse(
        status_code=404,
        content={"ok": False, "error": {"message": "CHANGELOG.md not found", "code": "not_found"}},
    )
