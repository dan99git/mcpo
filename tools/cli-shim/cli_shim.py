#!/usr/bin/env python3
"""Generic spec-driven CLI-to-MCP stdio shim.

Loads a JSON spec describing a command-line tool's subcommands and exposes
each subcommand as an MCP tool over stdio, using the low-level mcp.server.Server
API (not FastMCP).

Usage:
    python cli_shim.py <path-to-spec.json>

See README.md in this directory for the spec file format.
"""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

DEFAULT_TIMEOUT_SEC = 120
ALLOWED_PARAM_TYPES = {"string", "integer", "number", "boolean"}
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


class SpecError(Exception):
    """Raised for a bad spec file. Always fatal at startup."""


class ShimError(Exception):
    """Raised for a bad tool call. Always reported as an MCP tool error."""


def fail_startup(message: str) -> None:
    print(f"cli_shim: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Spec loading and validation
# ---------------------------------------------------------------------------

def load_spec(spec_path: Path) -> dict:
    if not spec_path.is_file():
        raise SpecError(f"spec file not found: {spec_path}")

    try:
        raw = spec_path.read_text(encoding="utf-8")
    except OSError as e:
        raise SpecError(f"could not read spec file {spec_path}: {e}") from e

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SpecError(f"spec file {spec_path} is not valid JSON: {e}") from e

    validate_spec(spec, spec_path)
    return spec


def validate_spec(spec: dict, spec_path: Path) -> None:
    if not isinstance(spec, dict):
        raise SpecError(f"spec file {spec_path}: top level must be a JSON object")

    for key in ("name", "description", "command", "tools"):
        if key not in spec:
            raise SpecError(f"spec file {spec_path}: missing required top-level key '{key}'")

    if not isinstance(spec["command"], str) or not spec["command"]:
        raise SpecError(f"spec file {spec_path}: 'command' must be a non-empty string")

    if not isinstance(spec["tools"], list) or not spec["tools"]:
        raise SpecError(f"spec file {spec_path}: 'tools' must be a non-empty list")

    if "default_timeout_sec" in spec and not isinstance(spec["default_timeout_sec"], (int, float)):
        raise SpecError(f"spec file {spec_path}: 'default_timeout_sec' must be a number")

    if "workdir" in spec and not isinstance(spec["workdir"], str):
        raise SpecError(f"spec file {spec_path}: 'workdir' must be a string")

    seen_names = set()
    for tool in spec["tools"]:
        validate_tool(tool, spec_path)
        if tool["name"] in seen_names:
            raise SpecError(f"spec file {spec_path}: duplicate tool name '{tool['name']}'")
        seen_names.add(tool["name"])


def validate_tool(tool: dict, spec_path: Path) -> None:
    if not isinstance(tool, dict):
        raise SpecError(f"spec file {spec_path}: each entry in 'tools' must be an object")

    for key in ("name", "description", "args"):
        if key not in tool:
            raise SpecError(f"spec file {spec_path}: tool {tool.get('name', '<unnamed>')} missing required key '{key}'")

    name = tool["name"]

    if not isinstance(tool["args"], list) or not all(isinstance(t, str) for t in tool["args"]):
        raise SpecError(f"tool '{name}': 'args' must be a list of strings")

    if "timeout_sec" in tool and not isinstance(tool["timeout_sec"], (int, float)):
        raise SpecError(f"tool '{name}': 'timeout_sec' must be a number")

    params = tool.get("params", {})
    if not isinstance(params, dict):
        raise SpecError(f"tool '{name}': 'params' must be an object")
    tool["params"] = params  # normalize: guarantee key exists downstream

    for pname, pdef in params.items():
        if not isinstance(pdef, dict):
            raise SpecError(f"tool '{name}' param '{pname}': must be an object")

        for key in ("type", "description"):
            if key not in pdef:
                raise SpecError(f"tool '{name}' param '{pname}': missing required key '{key}'")

        if pdef["type"] not in ALLOWED_PARAM_TYPES:
            raise SpecError(
                f"tool '{name}' param '{pname}': type '{pdef['type']}' not one of {sorted(ALLOWED_PARAM_TYPES)}"
            )

        required = pdef.get("required", False)
        if not isinstance(required, bool):
            raise SpecError(f"tool '{name}' param '{pname}': 'required' must be a boolean")

        if not required:
            if "args" not in pdef:
                raise SpecError(f"tool '{name}' param '{pname}': optional params must define an 'args' key")
            if not isinstance(pdef["args"], list) or not all(isinstance(t, str) for t in pdef["args"]):
                raise SpecError(f"tool '{name}' param '{pname}': 'args' must be a list of strings")

    # Validate that every placeholder in the tool's base args refers to a
    # required param defined on this tool. Fail loudly at startup, not at
    # call time, so a broken spec never reaches list_tools/call_tool.
    param_names = set(params.keys())
    required_names = {p for p, d in params.items() if d.get("required", False)}
    for token in tool["args"]:
        for placeholder in PLACEHOLDER_RE.findall(token):
            if placeholder not in param_names:
                raise SpecError(
                    f"tool '{name}': base args token '{token}' references unknown param '{{{placeholder}}}'"
                )
            if placeholder not in required_names:
                raise SpecError(
                    f"tool '{name}': base args token '{token}' references param '{placeholder}' "
                    "which is not marked required"
                )

    # Validate placeholders inside optional params' own arg groups.
    for pname, pdef in params.items():
        if pdef.get("required", False):
            continue
        for token in pdef["args"]:
            for placeholder in PLACEHOLDER_RE.findall(token):
                if placeholder not in param_names:
                    raise SpecError(
                        f"tool '{name}' param '{pname}': args token '{token}' references "
                        f"unknown param '{{{placeholder}}}'"
                    )


# ---------------------------------------------------------------------------
# MCP tool schema construction
# ---------------------------------------------------------------------------

def build_input_schema(tool: dict) -> dict:
    properties = {}
    required = []
    for pname, pdef in tool["params"].items():
        properties[pname] = {"type": pdef["type"], "description": pdef["description"]}
        if pdef.get("required", False):
            required.append(pname)

    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def build_mcp_tools(spec: dict) -> list:
    return [
        types.Tool(
            name=tool["name"],
            description=tool["description"],
            inputSchema=build_input_schema(tool),
        )
        for tool in spec["tools"]
    ]


# ---------------------------------------------------------------------------
# Tool call execution
# ---------------------------------------------------------------------------

def substitute(token: str, values: dict) -> str:
    """Replace every {name} placeholder in token with str(values[name]).

    Callers are expected to have already validated that every placeholder
    name is a known param with an available value; this raises ShimError
    defensively if that invariant is ever violated.
    """

    def repl(match: "re.Match[str]") -> str:
        pname = match.group(1)
        if pname not in values:
            raise ShimError(f"unknown or unavailable placeholder '{{{pname}}}' in args token '{token}'")
        return str(values[pname])

    return PLACEHOLDER_RE.sub(repl, token)


def build_argv(spec: dict, tool: dict, arguments: dict) -> list:
    params = tool["params"]
    required_names = {p for p, d in params.items() if d.get("required", False)}
    optional_names_in_order = [p for p, d in params.items() if not d.get("required", False)]

    # Unknown supplied param = error.
    for supplied in arguments:
        if supplied not in params:
            raise ShimError(f"tool '{tool['name']}': unknown parameter '{supplied}'")

    # Missing required param = error.
    missing = [p for p in required_names if p not in arguments or arguments[p] is None]
    if missing:
        raise ShimError(f"tool '{tool['name']}': missing required parameter(s): {', '.join(missing)}")

    argv = [spec["command"]]

    # Base args: required-param placeholders substituted in declared order.
    for token in tool["args"]:
        argv.append(substitute(token, arguments))

    # Optional params, in the order they appear in the spec's params object.
    for pname in optional_names_in_order:
        if pname not in arguments:
            continue
        value = arguments[pname]
        pdef = params[pname]

        if pdef["type"] == "boolean":
            if value is True:
                for token in pdef["args"]:
                    argv.append(substitute(token, arguments))
            # false / absent -> append nothing
        else:
            if value is None:
                continue
            for token in pdef["args"]:
                argv.append(substitute(token, arguments))

    return argv


def error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


def run_tool(spec: dict, tool: dict, arguments: dict) -> types.CallToolResult:
    try:
        argv = build_argv(spec, tool, arguments)
    except ShimError as e:
        return error_result(str(e))

    timeout = tool.get("timeout_sec")
    if timeout is None:
        timeout = spec.get("default_timeout_sec", DEFAULT_TIMEOUT_SEC)
    cwd = spec.get("workdir")

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            shell=False,
        )
    except FileNotFoundError as e:
        return error_result(f"tool '{tool['name']}': failed to launch command: {e}")
    except OSError as e:
        return error_result(f"tool '{tool['name']}': failed to launch command: {e}")
    except subprocess.TimeoutExpired:
        return error_result(f"tool '{tool['name']}': command timed out after {timeout}s")

    text = (
        f"exit code: {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n"
    )
    # Non-zero exit is data, not an MCP error: the underlying CLI may use its
    # exit code to carry meaning (e.g. kicad-cli --exit-code-violations).
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=False)


# ---------------------------------------------------------------------------
# Server wiring
# ---------------------------------------------------------------------------

def build_server(spec: dict) -> Server:
    server = Server(spec["name"], instructions=spec["description"])
    tools_by_name = {tool["name"]: tool for tool in spec["tools"]}

    @server.list_tools()
    async def list_tools() -> list:
        return build_mcp_tools(spec)

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
        tool = tools_by_name.get(name)
        if tool is None:
            return error_result(f"unknown tool: '{name}'")
        return run_tool(spec, tool, arguments or {})

    return server


async def run_server(spec: dict) -> None:
    server = build_server(spec)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=spec["name"],
                server_version=spec.get("version", "1.0.0"),
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    if len(sys.argv) != 2:
        fail_startup("usage: python cli_shim.py <path-to-spec.json>")

    spec_path = Path(sys.argv[1])
    try:
        spec = load_spec(spec_path)
    except SpecError as e:
        fail_startup(str(e))
        return  # unreachable, fail_startup exits

    asyncio.run(run_server(spec))


if __name__ == "__main__":
    main()
