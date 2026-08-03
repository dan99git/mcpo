"""BOS tool visibility and execution policy for native MCP requests.

FastMCP invokes this middleware only after the MCP SDK has validated and typed the
request. Wire protocol validation, discovery, caching, streaming, and transport
errors remain owned by the SDK.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Dict, Optional

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult

from mcpo.services.state import get_state_manager

logger = logging.getLogger(__name__)


class MCPToolFilterMiddleware(Middleware):
    """Filter listed tools and block disabled calls after protocol validation."""

    def __init__(
        self,
        app: Any = None,
        server_name: Optional[str] = None,
        server_names: Optional[Sequence[str]] = None,
    ) -> None:
        # ``app`` is accepted for compatibility with callers that constructed the
        # former ASGI middleware directly. It is intentionally unused.
        self.server_name = server_name
        normalized_names = {str(name) for name in (server_names or ()) if name}
        self.server_names = tuple(
            sorted(normalized_names, key=lambda name: (-len(name), name))
        )
        self.state_manager = get_state_manager()

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        self.state_manager.refresh_if_changed()
        tools = await call_next(context)
        filtered: list[Tool] = []
        for tool in tools:
            annotations = None
            if tool.annotations is not None:
                annotations = tool.annotations.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
            server_name, state_tool_name = self._resolve_tool_name(
                tool.name,
                annotations,
            )
            if self._is_tool_available(server_name, state_tool_name):
                filtered.append(tool)

        filtered.sort(key=lambda item: item.name)
        logger.info(
            "Filtered tools for '%s': %d -> %d tools",
            self.server_name or "aggregate proxy",
            len(tools),
            len(filtered),
        )
        return filtered

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        self.state_manager.refresh_if_changed()
        tool_name = context.message.name
        server_name, state_tool_name = self._resolve_tool_name(tool_name)
        if self._is_tool_available(server_name, state_tool_name):
            return await call_next(context)

        logger.warning("Blocked call to disabled tool: %s/%s", server_name, tool_name)
        return ToolResult(
            content=[
                mt.TextContent(
                    type="text",
                    text=f"Tool '{tool_name}' is disabled",
                )
            ],
            is_error=True,
            meta={
                "mcpo": {
                    "code": "tool_disabled",
                    "tool": tool_name,
                    "server": server_name,
                }
            },
        )

    def _is_tool_available(self, server_name: str, tool_name: str) -> bool:
        if not server_name:
            return not self.server_names
        return (
            self.state_manager.is_server_enabled(server_name)
            and self.state_manager.is_tool_enabled(server_name, tool_name)
        )

    def _resolve_tool_name(
        self,
        tool_name: str,
        annotations: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, str]:
        if self.server_name:
            return self.server_name, tool_name

        annotated_server = annotations.get("server") if annotations else None
        if not isinstance(annotated_server, str):
            annotated_server = None
        if self.server_names:
            matches = [
                (server_name, tool_name[len(server_name) + 1 :])
                for server_name in self.server_names
                if tool_name.startswith(f"{server_name}_")
            ]
            if len(matches) == 1:
                return matches[0]
            return "", tool_name


        state = self.state_manager.get_all_states()
        if isinstance(state, dict):
            ordered_servers = sorted(state, key=len, reverse=True)
            for server_name in ordered_servers:
                server_data = state.get(server_name, {})
                state_tools = (
                    server_data.get("tools", {})
                    if isinstance(server_data, dict)
                    else {}
                )
                if tool_name in state_tools:
                    return server_name, tool_name
                for separator in ("__", "_"):
                    prefix = f"{server_name}{separator}"
                    if tool_name.startswith(prefix):
                        candidate = tool_name[len(prefix):]
                        if candidate in state_tools:
                            return server_name, candidate

            for server_name in ordered_servers:
                for separator in ("__", "_"):
                    prefix = f"{server_name}{separator}"
                    if tool_name.startswith(prefix):
                        return server_name, tool_name[len(prefix):]

        if annotated_server:
            for separator in ("__", "_"):
                prefix = f"{annotated_server}{separator}"
                if tool_name.startswith(prefix):
                    return annotated_server, tool_name[len(prefix):]
            return annotated_server, tool_name

        if "__" in tool_name:
            server_name, state_tool_name = tool_name.split("__", 1)
            return server_name, state_tool_name
        if "_" in tool_name:
            server_name, state_tool_name = tool_name.split("_", 1)
            return server_name, state_tool_name
        return "", tool_name

    def _find_server_for_tool(self, tool_name: str) -> str:
        """Return the state server resolved for an exposed tool name."""
        server_name, _ = self._resolve_tool_name(tool_name)
        return server_name
