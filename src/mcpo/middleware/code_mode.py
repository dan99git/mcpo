"""Code-mode policy for native FastMCP tool operations.

The middleware exposes two catalog meta-tools when code mode is enabled. It runs
after MCP SDK transport and request validation, so it never parses or rewrites raw
JSON-RPC bodies.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Dict, List, Optional

import mcp_types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import FunctionTool, Tool, ToolResult

from mcpo.services.code_mode import (
    CatalogEntry,
    build_catalog,
    filter_enabled_entries,
    get_code_mode_tool_definitions,
    search_catalog,
)
from mcpo.services.state import get_state_manager

logger = logging.getLogger(__name__)


async def _unreachable_meta_tool(**_arguments: Any) -> None:
    raise RuntimeError("Code-mode meta-tools must be handled by middleware")


def _build_meta_tools() -> list[Tool]:
    tools: list[Tool] = []
    for definition in get_code_mode_tool_definitions():
        tools.append(
            FunctionTool(
                name=definition["name"],
                description=definition.get("description"),
                parameters=definition["inputSchema"],
                output_schema=None,
                fn=_unreachable_meta_tool,
            )
        )
    return tools


class CodeModeMCPMiddleware(Middleware):
    """Expose and dispatch code-mode meta-tools after MCP validation."""

    def __init__(
        self,
        app: Any = None,
        server_name: Optional[str] = None,
        server_names: Optional[Sequence[str]] = None,
    ) -> None:
        # ``app`` remains accepted for compatibility with direct unit callers of
        # the former ASGI middleware. Native FastMCP registration passes no app.
        self.server_name = server_name
        normalized_names = {str(name) for name in (server_names or ()) if name}
        self.server_names = tuple(
            sorted(normalized_names, key=lambda name: (-len(name), name))
        )
        self.state_manager = get_state_manager()
        self._catalog: List[CatalogEntry] = []
        self._catalog_built = False
        self._upstream_tools: Dict[str, List[Dict[str, Any]]] = {}
        self._meta_tools = _build_meta_tools()

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        self.state_manager.refresh_if_changed()
        tools = await call_next(context)
        if not self.state_manager.is_code_mode_enabled():
            return tools

        self._update_catalog_from_tools([self._tool_to_dict(tool) for tool in tools])
        logger.info(
            "Code mode: replaced %d tools with %d meta-tools",
            len(tools),
            len(self._meta_tools),
        )
        return list(self._meta_tools)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        self.state_manager.refresh_if_changed()
        if not self.state_manager.is_code_mode_enabled():
            return await call_next(context)

        tool_name = context.message.name
        if tool_name not in {"search_tools", "execute_tool"}:
            return await call_next(context)

        arguments = context.message.arguments or {}
        if not isinstance(arguments, dict):
            return self._tool_result(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "Error: tool arguments must be an object",
                        }
                    ],
                    "isError": True,
                }
            )

        if tool_name == "search_tools":
            return self._tool_result(self.handle_search_tools(arguments))

        error_result = self.handle_execute_tool(arguments)
        if error_result is not None:
            return self._tool_result(error_result)

        qualified_name = arguments["tool"]
        entry = next(
            item for item in self._catalog if item.qualified_name == qualified_name
        )
        upstream_name = entry.upstream_name
        if not upstream_name:
            upstream_name = (
                entry.tool_name
                if self.server_name
                else f"{entry.server_name}_{entry.tool_name}"
            )

        rewritten = context.message.model_copy(
            update={
                "name": upstream_name,
                "arguments": arguments.get("arguments", {}),
            }
        )
        return await call_next(context.copy(message=rewritten))

    @staticmethod
    def _tool_to_dict(tool: Tool) -> Dict[str, Any]:
        return tool.to_mcp_tool().model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    @staticmethod
    def _tool_result(payload: Dict[str, Any]) -> ToolResult:
        content: list[mt.ContentBlock] = []
        for block in payload.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                content.append(
                    mt.TextContent(type="text", text=str(block.get("text", "")))
                )
        return ToolResult(
            content=content,
            is_error=bool(payload.get("isError", False)),
        )

    def _update_catalog_from_tools(self, tools: List[Dict[str, Any]]) -> None:
        tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            upstream_name = tool.get("name", "")
            if not isinstance(upstream_name, str) or not upstream_name:
                continue
            server_part, tool_part = self._split_upstream_tool_name(tool)
            if not server_part:
                logger.warning(
                    "Code mode: omitted ambiguous upstream tool '%s'",
                    upstream_name,
                )
                continue
            tool_copy = dict(tool)
            tool_copy["name"] = tool_part
            tool_copy["_mcpo_upstream_name"] = upstream_name
            tools_by_server.setdefault(server_part, []).append(tool_copy)

        self._upstream_tools = tools_by_server
        self._catalog = build_catalog(tools_by_server)
        self._catalog_built = True
        logger.info("Code mode: built catalog with %d tools", len(self._catalog))

    def _split_upstream_tool_name(
        self,
        tool: Dict[str, Any],
    ) -> tuple[str, str]:
        upstream_name = tool["name"]
        if self.server_name:
            return self.server_name, upstream_name

        annotations = tool.get("annotations")
        annotated_server = (
            annotations.get("server")
            if isinstance(annotations, dict)
            else None
        )
        if not isinstance(annotated_server, str):
            annotated_server = None
        if self.server_names:
            matches = [
                (server_name, upstream_name[len(server_name) + 1 :])
                for server_name in self.server_names
                if upstream_name.startswith(f"{server_name}_")
            ]
            if len(matches) == 1:
                return matches[0]
            return "", upstream_name


        states = self.state_manager.get_all_states()
        if isinstance(states, dict):
            ordered_servers = sorted(states, key=len, reverse=True)
            for server_name in ordered_servers:
                server_data = states.get(server_name, {})
                state_tools = (
                    server_data.get("tools", {})
                    if isinstance(server_data, dict)
                    else {}
                )
                if upstream_name in state_tools:
                    return server_name, upstream_name
                for separator in ("__", "_"):
                    prefix = f"{server_name}{separator}"
                    if upstream_name.startswith(prefix):
                        candidate = upstream_name[len(prefix):]
                        if candidate in state_tools:
                            return server_name, candidate

            for server_name in ordered_servers:
                for separator in ("__", "_"):
                    prefix = f"{server_name}{separator}"
                    if upstream_name.startswith(prefix):
                        return server_name, upstream_name[len(prefix):]

        if annotated_server:
            for separator in ("__", "_"):
                prefix = f"{annotated_server}{separator}"
                if upstream_name.startswith(prefix):
                    return annotated_server, upstream_name[len(prefix):]
            return annotated_server, upstream_name

        if "__" in upstream_name:
            return tuple(upstream_name.split("__", 1))
        if "_" in upstream_name:
            return tuple(upstream_name.split("_", 1))
        return "unknown", upstream_name

    def handle_search_tools(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(arguments, dict):
            arguments = {}
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        visible = filter_enabled_entries(self._catalog, self.state_manager)
        results = search_catalog(
            visible,
            query,
            limit=limit,
            state_manager=self.state_manager,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"tools": results, "total_available": len(visible)},
                        indent=2,
                    ),
                }
            ],
            "isError": False,
        }

    def handle_execute_tool(self, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(arguments, dict):
            arguments = {}
        tool_qualified = arguments.get("tool", "")
        tool_args = arguments.get("arguments", {})

        if not isinstance(tool_qualified, str) or not tool_qualified:
            return {
                "content": [
                    {"type": "text", "text": "Error: 'tool' parameter is required"}
                ],
                "isError": True,
            }
        if not isinstance(tool_args, dict):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: 'arguments' parameter must be an object",
                    }
                ],
                "isError": True,
            }

        entry = next(
            (
                catalog_entry
                for catalog_entry in self._catalog
                if catalog_entry.qualified_name == tool_qualified
            ),
            None,
        )
        if entry is None:
            if not self._catalog_built:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Error: Tool catalog is not available yet",
                        }
                    ],
                    "isError": True,
                }
            suggestions = search_catalog(
                self._catalog,
                tool_qualified,
                limit=3,
                state_manager=self.state_manager,
            )
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n\nDid you mean:\n" + "\n".join(
                    f"  - {item['tool']}: {item['description'][:80]}"
                    for item in suggestions
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Error: Tool '{tool_qualified}' not found in catalog."
                            f"{suggestion_text}"
                        ),
                    }
                ],
                "isError": True,
            }

        if (
            not self.state_manager.is_server_enabled(entry.server_name)
            or not self.state_manager.is_tool_enabled(
                entry.server_name,
                entry.tool_name,
            )
        ):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Tool '{tool_qualified}' is disabled",
                    }
                ],
                "isError": True,
            }

        return None
