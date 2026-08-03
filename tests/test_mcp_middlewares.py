"""Unit tests for native FastMCP BOS policy middleware."""

from __future__ import annotations

import json
from typing import Any

import mcp_types as mt
import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools import Tool, ToolResult

from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.middleware.mcp_tool_filter import MCPToolFilterMiddleware


class FakeStateManager:
    def __init__(
        self,
        *,
        code_mode: bool = False,
        states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.code_mode = code_mode
        self.states = states or {}
        self.refresh_calls = 0

    def refresh_if_changed(self) -> bool:
        self.refresh_calls += 1
        return False

    def is_code_mode_enabled(self) -> bool:
        return self.code_mode

    def is_server_enabled(self, server_name: str) -> bool:
        return self.states.get(server_name, {}).get("enabled", True)

    def is_tool_enabled(self, server_name: str, tool_name: str) -> bool:
        return self.states.get(server_name, {}).get("tools", {}).get(tool_name, True)

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        return self.states


def make_tool(name: str, description: str = "") -> Tool:
    async def implementation() -> str:
        return name

    return Tool.from_function(
        implementation,
        name=name,
        description=description,
        output_schema=None,
    )


def list_context() -> MiddlewareContext:
    return MiddlewareContext(
        message=mt.ListToolsRequest(method="tools/list"),
        method="tools/list",
    )


def call_context(
    name: str,
    arguments: dict[str, Any],
    *,
    meta: dict[str, Any] | None = None,
) -> MiddlewareContext:
    params = mt.CallToolRequestParams.model_validate(
        {
            "name": name,
            "arguments": arguments,
            **({"_meta": meta} if meta is not None else {}),
        }
    )
    return MiddlewareContext(message=params, method="tools/call")


@pytest.mark.asyncio
async def test_filter_lists_only_enabled_tools_in_stable_order() -> None:
    middleware = MCPToolFilterMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        states={
            "alpha": {
                "enabled": True,
                "tools": {"safe": True, "danger": False, "zeta": True},
            }
        }
    )

    async def call_next(_context):
        return [make_tool("zeta"), make_tool("danger"), make_tool("safe")]

    tools = await middleware.on_list_tools(list_context(), call_next)

    assert [tool.name for tool in tools] == ["safe", "zeta"]


@pytest.mark.asyncio
async def test_filter_blocks_disabled_server_with_empty_tool_state() -> None:
    middleware = MCPToolFilterMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": False, "tools": {}}}
    )

    async def list_next(_context):
        return [make_tool("danger")]

    assert await middleware.on_list_tools(list_context(), list_next) == []

    provider_called = False

    async def call_next(_context):
        nonlocal provider_called
        provider_called = True
        return ToolResult(content="unexpected")

    result = await middleware.on_call_tool(
        call_context("danger", {}),
        call_next,
    )

    assert provider_called is False
    assert result.is_error is True


@pytest.mark.asyncio
async def test_aggregate_filter_prefers_configured_namespace_over_state_collision() -> None:
    middleware = MCPToolFilterMiddleware(
        server_names=("alpha", "longserver"),
    )
    middleware.state_manager = FakeStateManager(
        states={
            "alpha": {"enabled": True, "tools": {"danger": False}},
            "longserver": {"enabled": True, "tools": {"alpha_danger": True}},
        }
    )
    provider_called = False

    async def call_next(_context):
        nonlocal provider_called
        provider_called = True
        return ToolResult(content="unexpected")

    result = await middleware.on_call_tool(
        call_context("alpha_danger", {}),
        call_next,
    )

    assert provider_called is False
    assert result.is_error is True
    assert result.meta["mcpo"]["server"] == "alpha"
    assert result.meta["mcpo"]["tool"] == "alpha_danger"


@pytest.mark.asyncio
async def test_filter_blocks_disabled_call_without_invoking_provider() -> None:
    middleware = MCPToolFilterMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"danger": False}}}
    )
    provider_called = False

    async def call_next(_context):
        nonlocal provider_called
        provider_called = True
        return ToolResult(content="unexpected")

    result = await middleware.on_call_tool(
        call_context("danger", {}),
        call_next,
    )

    assert provider_called is False
    assert result.is_error is True
    assert result.meta == {
        "mcpo": {
            "code": "tool_disabled",
            "tool": "danger",
            "server": "alpha",
        }
    }


@pytest.mark.asyncio
async def test_code_mode_catalog_uses_filtered_tools_and_exposes_meta_tools() -> None:
    middleware = CodeModeMCPMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        code_mode=True,
        states={"alpha": {"enabled": True, "tools": {"safe": True}}},
    )

    async def call_next(_context):
        return [make_tool("safe", "Safe tool")]

    tools = await middleware.on_list_tools(list_context(), call_next)

    assert [tool.name for tool in tools] == ["search_tools", "execute_tool"]
    assert [entry.qualified_name for entry in middleware._catalog] == ["alpha.safe"]


def test_code_mode_catalog_prefers_configured_namespace_over_state_collision() -> None:
    middleware = CodeModeMCPMiddleware(
        server_names=("alpha", "longserver"),
    )
    middleware.state_manager = FakeStateManager(
        states={
            "alpha": {"enabled": True, "tools": {"danger": False}},
            "longserver": {"enabled": True, "tools": {"alpha_danger": True}},
        }
    )
    middleware._update_catalog_from_tools(
        [
            {
                "name": "alpha_danger",
                "description": "",
                "inputSchema": {"type": "object"},
            }
        ]
    )

    assert [entry.qualified_name for entry in middleware._catalog] == ["alpha.danger"]


@pytest.mark.asyncio
async def test_code_mode_search_is_synthetic_and_clamps_limit() -> None:
    middleware = CodeModeMCPMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        code_mode=True,
        states={
            "alpha": {
                "enabled": True,
                "tools": {f"tool_{index}": True for index in range(150)},
            }
        },
    )
    middleware._update_catalog_from_tools(
        [
            {
                "name": f"tool_{index}",
                "description": "",
                "inputSchema": {"type": "object"},
            }
            for index in range(150)
        ]
    )

    async def forbidden(_context):
        raise AssertionError("provider must not receive search_tools")

    result = await middleware.on_call_tool(
        call_context("search_tools", {"query": "", "limit": 1000}),
        forbidden,
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert len(payload["tools"]) == 100


@pytest.mark.asyncio
async def test_code_mode_execute_rewrites_typed_params_and_preserves_meta() -> None:
    middleware = CodeModeMCPMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(
        code_mode=True,
        states={"alpha": {"enabled": True, "tools": {"safe": True}}},
    )
    middleware._update_catalog_from_tools(
        [
            {
                "name": "safe",
                "description": "Safe tool",
                "inputSchema": {"type": "object"},
            }
        ]
    )
    captured = None

    async def call_next(context):
        nonlocal captured
        captured = context.message
        return ToolResult(content="ok")

    result = await middleware.on_call_tool(
        call_context(
            "execute_tool",
            {"tool": "alpha.safe", "arguments": {"value": 42}},
            meta={"vendor.example/trace": "abc"},
        ),
        call_next,
    )

    assert result.is_error is False
    assert captured.name == "safe"
    assert captured.arguments == {"value": 42}
    assert captured.meta == {"vendor.example/trace": "abc"}


@pytest.mark.asyncio
async def test_code_mode_disabled_passes_typed_call_unchanged() -> None:
    middleware = CodeModeMCPMiddleware(server_name="alpha")
    middleware.state_manager = FakeStateManager(code_mode=False)
    original = call_context("safe", {"value": 1})
    captured = None

    async def call_next(context):
        nonlocal captured
        captured = context
        return ToolResult(content="ok")

    await middleware.on_call_tool(original, call_next)

    assert captured is original


@pytest.mark.asyncio
async def test_aggregate_filter_fails_closed_on_ambiguous_namespace() -> None:
    middleware = MCPToolFilterMiddleware(server_names=("foo", "foo_bar"))
    middleware.state_manager = FakeStateManager(
        states={
            "foo": {"enabled": True, "tools": {"bar_baz": False}},
            "foo_bar": {"enabled": True, "tools": {"baz": True}},
        }
    )
    provider_called = False

    async def call_next(_context):
        nonlocal provider_called
        provider_called = True
        return ToolResult(content="unexpected")

    result = await middleware.on_call_tool(
        call_context("foo_bar_baz", {}),
        call_next,
    )

    assert provider_called is False
    assert result.is_error is True
    assert result.meta["mcpo"]["server"] == ""


@pytest.mark.asyncio
async def test_aggregate_filter_preserves_leading_underscore_tool_name() -> None:
    middleware = MCPToolFilterMiddleware(server_names=("foo", "other"))
    middleware.state_manager = FakeStateManager(
        states={"foo": {"enabled": True, "tools": {"_danger": False}}}
    )
    provider_called = False

    async def call_next(_context):
        nonlocal provider_called
        provider_called = True
        return ToolResult(content="unexpected")

    result = await middleware.on_call_tool(
        call_context("foo__danger", {}),
        call_next,
    )

    assert provider_called is False
    assert result.is_error is True
    assert result.meta["mcpo"]["server"] == "foo"


def test_code_mode_omits_ambiguous_namespace_from_catalog() -> None:
    middleware = CodeModeMCPMiddleware(server_names=("foo", "foo_bar"))
    middleware.state_manager = FakeStateManager(code_mode=True)
    middleware._update_catalog_from_tools(
        [
            {
                "name": "foo_bar_baz",
                "description": "",
                "inputSchema": {"type": "object"},
            }
        ]
    )

    assert middleware._catalog == []


def test_code_mode_preserves_leading_underscore_tool_name() -> None:
    middleware = CodeModeMCPMiddleware(server_names=("foo", "other"))
    middleware.state_manager = FakeStateManager(code_mode=True)
    middleware._update_catalog_from_tools(
        [
            {
                "name": "foo__danger",
                "description": "",
                "inputSchema": {"type": "object"},
            }
        ]
    )

    assert [entry.qualified_name for entry in middleware._catalog] == [
        "foo._danger"
    ]


@pytest.mark.asyncio
async def test_fastmcp_namespace_collision_lists_duplicate_and_dispatches_first() -> None:
    first = FastMCP("first")
    second = FastMCP("second")

    @first.tool(name="bar_baz")
    async def first_tool() -> str:
        return "FIRST:foo/bar_baz"

    @second.tool(name="baz")
    async def second_tool() -> str:
        return "SECOND:foo_bar/baz"

    aggregate = FastMCP("aggregate")
    aggregate.mount(first, namespace="foo")
    aggregate.mount(second, namespace="foo_bar")

    tools = await aggregate.list_tools()

    assert [tool.name for tool in tools] == ["foo_bar_baz", "foo_bar_baz"]
    assert all(tool.annotations is None for tool in tools)
    assert all("server" not in tool.meta for tool in tools)

    result = await aggregate.call_tool("foo_bar_baz", {})

    assert result.is_error is False
    assert len(result.content) == 1
    assert result.content[0].text == "FIRST:foo/bar_baz"
