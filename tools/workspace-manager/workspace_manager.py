#!/usr/bin/env python3
"""Stdio MCP server that switches mcpo's "active workspace".

Rewrites designated server entries in the mcpo config (args/env) so those
servers restart scoped to a new workspace root, then updates
workspace.active. Config path comes from the required MCPO_CONFIG_PATH
environment variable.

Uses the low-level mcp.server.Server API and mcp.server.stdio.stdio_server,
matching tools/cli-shim/cli_shim.py's convention (not FastMCP, even though
fastmcp is installed in this repo's venv).

Validation and config-rewrite logic live in plain functions below, separate
from the MCP tool wrappers, so they can be unit tested without spawning the
server (see tests/test_workspace_manager.py).
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

VALID_KINDS = {"arg", "env"}


class WorkspaceError(Exception):
    """Raised for any bad config, bad path, or bad target at call time."""


def fail_startup(message: str) -> None:
    print(f"workspace_manager: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config load / write
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.is_file():
        raise WorkspaceError(f"config file not found: {config_path}")

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise WorkspaceError(f"could not read config file {config_path}: {e}") from e

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        raise WorkspaceError(f"config file {config_path} is not valid JSON: {e}") from e

    if not isinstance(config, dict):
        raise WorkspaceError(f"config file {config_path}: top level must be a JSON object")

    return config


def write_config_atomic(config_path: Path, config: Dict[str, Any]) -> None:
    """Write temp file in the same dir, then os.replace over the target."""
    directory = config_path.parent
    fd, tmp_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{config_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, config_path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# workspace section validation
# ---------------------------------------------------------------------------

def validate_and_get_workspace(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the config's "workspace" dict, or raise WorkspaceError naming
    exactly what is missing/malformed."""
    workspace = config.get("workspace")
    if not isinstance(workspace, dict):
        raise WorkspaceError("config is missing a 'workspace' section")

    allowlist = workspace.get("allowlist")
    if not isinstance(allowlist, list) or len(allowlist) == 0:
        raise WorkspaceError("workspace.allowlist is missing, not a list, or empty")

    targets = workspace.get("targets")
    if not isinstance(targets, dict):
        raise WorkspaceError("workspace.targets is missing or not an object")

    return workspace


# ---------------------------------------------------------------------------
# path validation
# ---------------------------------------------------------------------------

def normalize_path_for_config(path_str: str) -> str:
    """Config path values use forward slashes (safe on Windows, matches most
    existing mcpo.json entries)."""
    return str(path_str).replace("\\", "/")


def validate_new_path(path_str: str, allowlist: List[str]) -> Path:
    """Validate path_str is an absolute, existing directory inside (or equal
    to) at least one allowlist parent. Returns the resolved (realpath) Path.

    Comparison is done on realpath'd Path objects via Path.is_relative_to,
    which compares path parts rather than raw strings, so it is immune to
    sibling-prefix tricks (e.g. allowlist "D:/foo" must not match "D:/foobar")
    and correctly resolves ".." traversal. On Windows, Path/WindowsPath part
    comparisons are already case-insensitive.
    """
    p = Path(path_str)

    if not p.is_absolute():
        raise WorkspaceError(f"path must be absolute: {path_str}")

    if not p.exists():
        raise WorkspaceError(f"path does not exist: {path_str}")

    if not p.is_dir():
        raise WorkspaceError(f"path is not a directory: {path_str}")

    resolved = Path(os.path.realpath(p))

    for parent in allowlist:
        resolved_parent = Path(os.path.realpath(Path(parent)))
        if resolved == resolved_parent or resolved.is_relative_to(resolved_parent):
            return resolved

    raise WorkspaceError(
        f"path {path_str} is not inside any workspace.allowlist directory: {allowlist}"
    )


# ---------------------------------------------------------------------------
# mcpServers container (shape-preserving)
# ---------------------------------------------------------------------------

def get_mcp_servers_container(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the mcpServers dict reference for whichever shape the file
    uses, so mutating it mutates `config` in place and preserves the file's
    original top-level shape on write.

    Supports both:
    - {"mcpServers": {...}}
    - {"config": {"mcpServers": {...}}, ...}
    """
    top = config.get("mcpServers")
    if isinstance(top, dict):
        return top

    nested = config.get("config")
    if isinstance(nested, dict) and isinstance(nested.get("mcpServers"), dict):
        return nested["mcpServers"]

    raise WorkspaceError(
        "config has no 'mcpServers' (checked top-level and nested 'config.mcpServers')"
    )


# ---------------------------------------------------------------------------
# target validation / rewrite
# ---------------------------------------------------------------------------

def validate_target_spec(
    server_name: str, target_spec: Any, mcp_servers: Dict[str, Any]
) -> None:
    """Validate one workspace.targets entry against the loaded mcpServers,
    without mutating anything. Raises WorkspaceError naming the problem."""
    if not isinstance(target_spec, dict):
        raise WorkspaceError(f"workspace.targets['{server_name}']: target spec must be an object")

    if server_name not in mcp_servers:
        raise WorkspaceError(f"target server '{server_name}' not found in mcpServers")

    if not isinstance(mcp_servers[server_name], dict):
        raise WorkspaceError(f"mcpServers['{server_name}'] is not an object")

    kind = target_spec.get("kind")
    if kind not in VALID_KINDS:
        raise WorkspaceError(
            f"target '{server_name}': unknown kind '{kind}' (expected 'arg' or 'env')"
        )

    if kind == "arg":
        position = target_spec.get("position")
        if position != "last":
            raise WorkspaceError(
                f"target '{server_name}': unsupported arg position '{position}' (only 'last' is supported)"
            )
        args = mcp_servers[server_name].get("args")
        if not isinstance(args, list) or len(args) == 0:
            raise WorkspaceError(
                f"target '{server_name}': server has no non-empty 'args' list to rewrite"
            )
    elif kind == "env":
        name = target_spec.get("name")
        if not isinstance(name, str) or not name:
            raise WorkspaceError(f"target '{server_name}': env target is missing a non-empty 'name'")


def apply_target(server_name: str, target_spec: Dict[str, Any], mcp_servers: Dict[str, Any], new_path: str) -> None:
    """Mutate mcp_servers[server_name] per target_spec. Assumes
    validate_target_spec already passed for this entry."""
    server_entry = mcp_servers[server_name]
    kind = target_spec["kind"]

    if kind == "arg":
        args = server_entry["args"]
        args[-1] = new_path
    elif kind == "env":
        name = target_spec["name"]
        env = server_entry.get("env")
        if not isinstance(env, dict):
            env = {}
            server_entry["env"] = env
        env[name] = new_path
    else:  # pragma: no cover - validate_target_spec already rejects this
        raise WorkspaceError(f"target '{server_name}': unknown kind '{kind}'")


# ---------------------------------------------------------------------------
# top-level operations (what the MCP tools call)
# ---------------------------------------------------------------------------

def get_workspace_info(config_path: Path) -> Dict[str, Any]:
    config = load_config(config_path)
    workspace = validate_and_get_workspace(config)
    return {
        "active": workspace.get("active"),
        "allowlist": workspace.get("allowlist"),
        "targets": workspace.get("targets"),
    }


def set_workspace_path(config_path: Path, new_path: str) -> Dict[str, Any]:
    config = load_config(config_path)
    workspace = validate_and_get_workspace(config)
    allowlist = workspace["allowlist"]
    targets = workspace["targets"]

    resolved = validate_new_path(new_path, allowlist)
    mcp_servers = get_mcp_servers_container(config)

    # Phase 1: validate every target before mutating anything. Unknown
    # server name or unknown kind aborts with no file (and no in-memory
    # config) change.
    for server_name, target_spec in targets.items():
        validate_target_spec(server_name, target_spec, mcp_servers)

    # Phase 2: apply. All entries already validated, so this cannot fail.
    normalized_path = normalize_path_for_config(str(resolved))
    updated_servers = []
    for server_name, target_spec in targets.items():
        apply_target(server_name, target_spec, mcp_servers, normalized_path)
        updated_servers.append(server_name)

    workspace["active"] = normalized_path

    write_config_atomic(config_path, config)

    return {"active": normalized_path, "updated_servers": updated_servers}


# ---------------------------------------------------------------------------
# MCP server wiring
# ---------------------------------------------------------------------------

def error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


def build_server(config_path: Path) -> Server:
    server = Server(
        "workspace-manager",
        instructions=(
            "Switch mcpo's active workspace: get_workspace() reports the current "
            "active path, allowlist, and targets; set_workspace(path) validates "
            "path against the allowlist and rewrites the configured target "
            "server args/env to point at it."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list:
        return [
            types.Tool(
                name="get_workspace",
                description="Return the current active workspace path, allowlist, and targets.",
                inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            types.Tool(
                name="set_workspace",
                description=(
                    "Switch the active workspace to an absolute, existing directory "
                    "inside the configured allowlist. Rewrites the configured target "
                    "servers' args/env and updates workspace.active."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to the new workspace root.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        arguments = arguments or {}
        try:
            if name == "get_workspace":
                result = get_workspace_info(config_path)
            elif name == "set_workspace":
                path = arguments.get("path")
                if not isinstance(path, str) or not path:
                    return error_result("set_workspace: 'path' is required and must be a non-empty string")
                result = set_workspace_path(config_path, path)
            else:
                return error_result(f"unknown tool: '{name}'")
        except WorkspaceError as e:
            return error_result(str(e))

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
            isError=False,
        )

    return server


async def run_server(config_path: Path) -> None:
    server = build_server(config_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="workspace-manager",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    config_path_str = os.environ.get("MCPO_CONFIG_PATH")
    if not config_path_str:
        fail_startup("MCPO_CONFIG_PATH environment variable is required but not set")
        return  # unreachable, fail_startup exits

    config_path = Path(config_path_str)
    if not config_path.exists():
        fail_startup(f"MCPO_CONFIG_PATH does not point to an existing file: {config_path}")
        return  # unreachable, fail_startup exits

    asyncio.run(run_server(config_path))


if __name__ == "__main__":
    main()
