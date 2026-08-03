"""
Admin router for server management and configuration operations.
"""
import logging
import re
from typing import Dict, Any
from fastapi import FastAPI, APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
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

async def _mount_or_remount_fastmcp(main_app: FastAPI, base_path: str = "/mcp") -> None:
    """Mount or remount FastMCP proxy http_app(s) at base_path if FastMCP is available.

    Shuts down the previously-mounted proxy apps' lifespans (which is what actually
    initializes FastMCP's StreamableHTTPSessionManager task group) before removing
    their routes, and enters the lifespan of each newly-built proxy app before
    mounting it, tracked on main_app.state.fastmcp_proxy_lifespans. Mounted sub-apps
    never get their lifespan auto-invoked by Starlette, so this is the only place
    that mechanism runs for these proxy mounts.
    """
    global _FASTMCP_AVAILABLE
    try:
        # Shut down any previously-entered proxy lifespans before their routes are
        # removed, so nothing is orphaned across a remount.
        old_lifespans = getattr(main_app.state, "fastmcp_proxy_lifespans", {}) or {}
        for key, cm in list(old_lifespans.items()):
            try:
                await cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.error("Error shutting down previous FastMCP proxy lifespan for '%s': %s", key, exc, exc_info=True)
        main_app.state.fastmcp_proxy_lifespans = {}

        # Remove any existing FastMCP proxy mounts first
        removed_count = 0
        for route in list(main_app.router.routes):
            route_app = getattr(route, "app", None)
            if route_app and hasattr(route_app, "state") and getattr(route_app.state, "is_fastmcp_proxy", False):
                main_app.router.routes.remove(route)
                removed_count += 1
                logger.info("Removed FastMCP proxy mount at %s", getattr(route, "path", "<unknown>"))

        # Determine availability lazily
        if _FASTMCP_AVAILABLE is None:
            try:
                from fastmcp.server.server import FastMCP  # type: ignore
                from fastmcp.mcp_config import MCPConfig  # type: ignore
                _FASTMCP_AVAILABLE = True
            except Exception:
                _FASTMCP_AVAILABLE = False

        if not _FASTMCP_AVAILABLE:
            logger.info("FastMCP not available; skipping proxy mount")
            main_app.state.fastmcp_proxy_mounts = []
            main_app.state.fastmcp_proxy_global_mount = None
            return

        # Retrieve config
        config_data = getattr(main_app.state, "config_data", None)
        if not isinstance(config_data, dict):
            logger.info("No config_data present; skipping FastMCP proxy mount")
            main_app.state.fastmcp_proxy_mounts = []
            main_app.state.fastmcp_proxy_global_mount = None
            return

        from mcpo.utils.config import interpolate_env_placeholders_in_config
        raw_cfg = interpolate_env_placeholders_in_config(config_data)

        # Drop servers disabled in config or in shared runtime state, so the
        # in-process proxy mount matches whatever else is honoring "enabled".
        state_manager = get_state_manager()
        all_servers_cfg = raw_cfg.get("mcpServers", {}) if isinstance(raw_cfg, dict) else {}
        enabled_servers_cfg: Dict[str, Any] = {}
        for server_name, server_cfg in all_servers_cfg.items():
            if isinstance(server_cfg, dict) and not server_cfg.get("enabled", True):
                logger.info("Skipping FastMCP proxy mount for '%s' (disabled in config)", server_name)
                continue
            if not state_manager.is_server_enabled(server_name):
                logger.info("Skipping FastMCP proxy mount for '%s' (disabled in shared state)", server_name)
                continue
            enabled_servers_cfg[server_name] = server_cfg
        if isinstance(raw_cfg, dict):
            raw_cfg = dict(raw_cfg)
            raw_cfg["mcpServers"] = enabled_servers_cfg

        # fastmcp 4: FastMCP.as_proxy removed; create_proxy is the replacement
        # (fastmcp/server/server.py:2506, exported in fastmcp/server/__init__.py:7).
        from fastmcp.server import create_proxy  # type: ignore
        from fastmcp.mcp_config import MCPConfig  # type: ignore

        cfg = MCPConfig.from_dict(raw_cfg)
        proxy = create_proxy(cfg)
        new_lifespans: Dict[str, Any] = {}

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
        api_key = getattr(main_app.state, "api_key", None)
        strict_auth = getattr(main_app.state, "strict_auth", False)
        api_key_middleware_cls = None
        if api_key and strict_auth:
            from mcpo.utils.auth import APIKeyMiddleware  # local import to avoid circular deps

            api_key_middleware_cls = APIKeyMiddleware

        def _secure_app(app_to_secure):
            if api_key_middleware_cls is None:
                return app_to_secure
            secure_wrapper = FastAPI()
            secure_wrapper.add_middleware(api_key_middleware_cls, api_key=api_key)
            secure_wrapper.mount("/", app_to_secure)
            setattr(secure_wrapper.state, "is_fastmcp_proxy", True)
            setattr(secure_wrapper.state, "proxy_scope", getattr(app_to_secure.state, "proxy_scope", None))
            if hasattr(app_to_secure.state, "proxy_server_name"):
                setattr(
                    secure_wrapper.state,
                    "proxy_server_name",
                    getattr(app_to_secure.state, "proxy_server_name"),
                )
            return secure_wrapper

        proxy_mounts: list[dict[str, Any]] = []
        used_paths: set[str] = set()

        from mcpo.middleware.code_mode import CodeModeMCPMiddleware
        from mcpo.middleware.mcp_tool_filter import MCPToolFilterMiddleware

        # Honor the app's configured stateless setting (same attribute the 8001 proxy
        # stores on its own app state) instead of hardcoding None, so a remount does
        # not silently revert a stateless deployment to stateful.
        stateless_http = getattr(main_app.state, "proxy_stateless", None)

        # Aggregate (global) proxy mount. Native middleware runs only after the
        # MCP SDK has validated and typed the request.
        aggregate_server_names = tuple(enabled_servers_cfg)
        proxy.add_middleware(
            CodeModeMCPMiddleware(
                server_name=None,
                server_names=aggregate_server_names,
            )
        )
        proxy.add_middleware(
            MCPToolFilterMiddleware(
                server_name=None,
                server_names=aggregate_server_names,
            )
        )
        global_app = proxy.http_app(
            path="/",
            transport="streamable-http",
            stateless_http=stateless_http,
            host_origin_protection=True,
        )
        setattr(global_app.state, "is_fastmcp_proxy", True)
        setattr(global_app.state, "proxy_scope", "global")
        setattr(global_app.state, "proxy_mount_base", base_path)

        global_mount_path = _compose_path(path_prefix, base_path)
        used_paths.add(global_mount_path)
        wrapped_global = _secure_app(global_app)
        try:
            global_cm = global_app.router.lifespan_context(global_app)
            await global_cm.__aenter__()
            new_lifespans[global_mount_path] = global_cm
        except Exception as exc:
            logger.error("Failed to start FastMCP proxy lifespan for aggregate mount: %s", exc, exc_info=True)
        main_app.mount(global_mount_path, wrapped_global)
        logger.info("Mounted aggregate FastMCP proxy at %s", global_mount_path)

        proxy_mounts.append(
            {
                "id": "global" if global_mount_path != "/" else "root",
                "label": "All MCPs",
                "path": global_mount_path,
                "scope": "global",
            }
        )

        servers = raw_cfg.get("mcpServers", {}) if isinstance(raw_cfg, dict) else {}
        for server_name, server_cfg in servers.items():
            mount_segment = str(server_name).strip().strip("/")
            if not mount_segment:
                logger.warning("Skipping MCP server with blank name in config")
                continue

            single_cfg_dict = {"mcpServers": {server_name: server_cfg}}
            try:
                single_cfg = MCPConfig.from_dict(single_cfg_dict)
                single_proxy = create_proxy(single_cfg)
            except Exception as exc:
                logger.error(
                    "Failed to initialize FastMCP proxy for server '%s': %s",
                    server_name,
                    exc,
                    exc_info=True,
                )
                continue

            single_proxy.add_middleware(
                CodeModeMCPMiddleware(server_name=server_name)
            )
            single_proxy.add_middleware(
                MCPToolFilterMiddleware(server_name=server_name)
            )
            sub_app = single_proxy.http_app(
                path="/",
                transport="streamable-http",
                stateless_http=stateless_http,
                host_origin_protection=True,
            )
            setattr(sub_app.state, "is_fastmcp_proxy", True)
            setattr(sub_app.state, "proxy_scope", "server")
            setattr(sub_app.state, "proxy_server_name", server_name)

            mount_path = _compose_path(path_prefix, base_path, mount_segment)
            if mount_path in used_paths:
                logger.warning(
                    "Skipping MCP server '%s' because mount path %s is already in use",
                    server_name,
                    mount_path,
                )
                continue

            used_paths.add(mount_path)
            wrapped_sub_app = _secure_app(sub_app)
            try:
                sub_cm = sub_app.router.lifespan_context(sub_app)
                await sub_cm.__aenter__()
                new_lifespans[mount_path] = sub_cm
            except Exception as exc:
                logger.error(
                    "Failed to start FastMCP proxy lifespan for server '%s': %s", server_name, exc, exc_info=True
                )
            main_app.mount(mount_path, wrapped_sub_app)
            logger.info("Mounted FastMCP proxy for server '%s' at %s", server_name, mount_path)

            label = server_name
            if isinstance(server_cfg, dict):
                label = server_cfg.get("name") or server_cfg.get("displayName") or server_name

            proxy_mounts.append(
                {
                    "id": mount_segment,
                    "label": label,
                    "path": mount_path,
                    "scope": "server",
                    "server": server_name,
                }
            )

        main_app.state.fastmcp_proxy_mounts = proxy_mounts
        main_app.state.fastmcp_proxy_global_mount = global_mount_path
        main_app.state.fastmcp_proxy_lifespans = new_lifespans
    except Exception as e:
        logger.error(f"Failed to mount FastMCP proxy: {e}", exc_info=True)



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
