"""
Unit tests for the request-side interception in CodeModeMCPMiddleware.

Covers the flows the origin/dev branch added ASGI-level tests for, ported to
the native FastMCP middleware hooks that the merged architecture registers
(FastMCPProxy.add_middleware dispatches on_list_tools/on_call_tool; there is
no ASGI request path anymore):

1. tools/call for search_tools -> synthesized response (no upstream hit)
2. tools/call for execute_tool with valid qualified name -> rewrites to the
   upstream aggregate tool name and forwards
3. tools/call for execute_tool with unknown tool -> synthesized error response
4. unrelated tools/call passes through unchanged
5. code mode disabled -> full pass-through
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import mcp_types as mt
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import ToolResult

from mcpo.middleware.code_mode import CodeModeMCPMiddleware


class FakeStateManager:
    def __init__(
        self,
        *,
        code_mode: bool = False,
        states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.code_mode = code_mode
        self.states = states or {}

    def refresh_if_changed(self) -> bool:
        return False

    def is_code_mode_enabled(self) -> bool:
        return self.code_mode

    def is_server_enabled(self, server_name: str) -> bool:
        return self.states.get(server_name, {}).get("enabled", True)

    def is_tool_enabled(self, server_name: str, tool_name: str) -> bool:
        return self.states.get(server_name, {}).get("tools", {}).get(tool_name, True)

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        return self.states


def call_context(name: str, arguments: dict[str, Any]) -> MiddlewareContext:
    params = mt.CallToolRequestParams.model_validate(
        {"name": name, "arguments": arguments}
    )
    return MiddlewareContext(message=params, method="tools/call")


def _make_middleware() -> CodeModeMCPMiddleware:
    middleware = CodeModeMCPMiddleware(
        server_names=("time", "brave-search"),
    )
    middleware.state_manager = FakeStateManager(
        code_mode=True,
        states={
            "time": {"enabled": True, "tools": {"get_current_time": True}},
            "brave-search": {"enabled": True, "tools": {"web_search": True}},
        },
    )
    middleware._update_catalog_from_tools(
        [
            {
                "name": "time_get_current_time",
                "description": "Return the current time in a timezone",
                "inputSchema": {
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
            },
            {
                "name": "brave-search_web_search",
                "description": "Search the web",
                "inputSchema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            },
        ]
    )
    return middleware


@pytest.mark.asyncio
async def test_search_tools_is_synthesized_without_upstream() -> None:
    middleware = _make_middleware()

    async def forbidden(_context):
        raise AssertionError("search_tools must not hit upstream")

    result = await middleware.on_call_tool(
        call_context("search_tools", {"query": "time"}),
        forbidden,
    )

    assert result.is_error is False
    inner = json.loads(result.content[0].text)
    assert any(t["tool"] == "time.get_current_time" for t in inner["tools"])


@pytest.mark.asyncio
async def test_execute_tool_rewrites_to_aggregate_name() -> None:
    middleware = _make_middleware()
    captured = None

    async def call_next(context):
        nonlocal captured
        captured = context.message
        return ToolResult(content="ok")

    result = await middleware.on_call_tool(
        call_context(
            "execute_tool",
            {"tool": "time.get_current_time", "arguments": {"timezone": "UTC"}},
        ),
        call_next,
    )

    assert result.is_error is False
    assert captured is not None, "execute_tool with valid name must forward upstream"
    assert captured.name == "time_get_current_time"
    assert captured.arguments == {"timezone": "UTC"}


@pytest.mark.asyncio
async def test_execute_tool_unknown_returns_synthesized_error() -> None:
    middleware = _make_middleware()

    async def forbidden(_context):
        raise AssertionError("unknown execute_tool must not reach upstream")

    result = await middleware.on_call_tool(
        call_context("execute_tool", {"tool": "bogus.nope"}),
        forbidden,
    )

    assert result.is_error is True
    assert "not found in catalog" in result.content[0].text


@pytest.mark.asyncio
async def test_unrelated_call_passes_through() -> None:
    middleware = _make_middleware()
    original = call_context("time_get_current_time", {"timezone": "UTC"})
    captured = None

    async def call_next(context):
        nonlocal captured
        captured = context
        return ToolResult(content="ok")

    await middleware.on_call_tool(original, call_next)

    assert captured is original


@pytest.mark.asyncio
async def test_middleware_noop_when_code_mode_disabled() -> None:
    middleware = CodeModeMCPMiddleware(server_names=("time",))
    middleware.state_manager = FakeStateManager(code_mode=False)
    original = call_context("search_tools", {"query": ""})
    captured = None

    async def call_next(context):
        nonlocal captured
        captured = context
        return ToolResult(content="ok")

    await middleware.on_call_tool(original, call_next)

    assert captured is original, "when code mode is off, search_tools must be forwarded unchanged"
