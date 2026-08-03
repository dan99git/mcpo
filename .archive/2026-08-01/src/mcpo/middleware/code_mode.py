"""
Code Mode MCP Middleware

When code mode is enabled, this middleware intercepts MCP protocol messages on the
FastMCP proxy (port 8001) to:

1. Replace tools/list responses with only search_tools + execute_tool
2. Intercept tools/call for search_tools → search the internal catalog
3. Intercept tools/call for execute_tool → route to the actual MCP server
4. Pass through all other MCP messages unchanged

This sits as ASGI middleware on the FastMCP proxy app, similar to MCPToolFilterMiddleware.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from mcpo.services.code_mode import (
    CatalogEntry,
    build_catalog,
    filter_enabled_entries,
    get_code_mode_tool_definitions,
    search_catalog,
)
from mcpo.services.state import get_state_manager

logger = logging.getLogger(__name__)


class CodeModeMCPMiddleware:
    """
    ASGI middleware that transforms MCP proxy behavior when code mode is active.

    When code mode is OFF: passes everything through unchanged.
    When code mode is ON:
      - tools/list → returns search_tools + execute_tool only
      - tools/call search_tools → searches internal catalog, returns results
      - tools/call execute_tool → rewrites to actual tool call, passes to upstream
    """

    def __init__(self, app: ASGIApp, server_name: Optional[str] = None):
        self.app = app
        self.server_name = server_name  # None = aggregate proxy
        self.state_manager = get_state_manager()
        self._catalog: List[CatalogEntry] = []
        self._catalog_built = False
        # Store original tools from upstream for catalog building
        self._upstream_tools: Dict[str, List[Dict[str, Any]]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self.state_manager.refresh_if_changed()

        if not self.state_manager.is_code_mode_enabled():
            await self.app(scope, receive, send)
            return

        request_messages, request_body = await self._read_request(receive)
        request_data: Any = None
        try:
            request_data = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if isinstance(request_data, list):
            batch_errors = self._batch_meta_tool_errors(request_data)
            if batch_errors:
                await self._send_json_response(send, batch_errors)
                return

        if isinstance(request_data, dict) and request_data.get("method") == "tools/call":
            synthetic, rewritten = self._process_tool_call_request(request_data)
            if synthetic is not None:
                await self._send_json_response(send, synthetic)
                return
            if rewritten is not None:
                rewritten_body = json.dumps(rewritten, separators=(",", ":")).encode()
                request_messages = [
                    {
                        "type": "http.request",
                        "body": rewritten_body,
                        "more_body": False,
                    }
                ]
                scope = self._scope_with_content_length(scope, len(rewritten_body))

        receive_replay = self._replay_receive(request_messages, receive)
        if not self._has_method(request_data, "tools/list"):
            await self.app(scope, receive_replay, send)
            return

        await self._call_with_tools_list_transform(scope, receive_replay, send)

    @staticmethod
    async def _read_request(receive: Receive) -> tuple[List[Dict[str, Any]], bytes]:
        """Read one HTTP request so it can be inspected and replayed upstream."""
        messages: List[Dict[str, Any]] = []
        body_parts: List[bytes] = []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body_parts.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        return messages, b"".join(body_parts)

    @staticmethod
    def _replay_receive(
        messages: List[Dict[str, Any]],
        receive: Receive,
    ) -> Receive:
        index = 0

        async def replay() -> Dict[str, Any]:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await receive()

        return replay

    @staticmethod
    def _has_method(data: Any, method: str) -> bool:
        if isinstance(data, list):
            return any(
                isinstance(message, dict) and message.get("method") == method
                for message in data
            )
        return isinstance(data, dict) and data.get("method") == method

    @staticmethod
    def _headers_with_content_length(
        headers: List[tuple[bytes, bytes]],
        content_length: int,
    ) -> List[tuple[bytes, bytes]]:
        updated = [
            (key, value)
            for key, value in headers
            if key.lower() != b"content-length"
        ]
        updated.append((b"content-length", str(content_length).encode()))
        return updated

    @classmethod
    def _scope_with_content_length(cls, scope: Scope, content_length: int) -> Scope:
        updated_scope = dict(scope)
        updated_scope["headers"] = cls._headers_with_content_length(
            list(scope.get("headers", [])),
            content_length,
        )
        return updated_scope

    @classmethod
    async def _send_json_response(
        cls,
        send: Send,
        payload: Any,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    def _process_tool_call_request(
        self,
        message: Dict[str, Any],
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        params = message.get("params")
        if not isinstance(params, dict):
            return None, None

        tool_name = params.get("name")
        if tool_name not in {"search_tools", "execute_tool"}:
            return None, None

        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: tool arguments must be an object",
                    }
                ],
                "isError": True,
            }
            return self._jsonrpc_result(message, result), None

        if tool_name == "search_tools":
            return self._jsonrpc_result(message, self.handle_search_tools(arguments)), None

        error_result = self.handle_execute_tool(arguments)
        if error_result is not None:
            return self._jsonrpc_result(message, error_result), None

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

        rewritten = dict(message)
        rewritten_params = dict(params)
        rewritten_params["name"] = upstream_name
        rewritten_params["arguments"] = arguments.get("arguments", {})
        rewritten["params"] = rewritten_params
        return None, rewritten

    def _batch_meta_tool_errors(
        self,
        messages: List[Any],
    ) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict) or message.get("method") != "tools/call":
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            if params.get("name") not in {"search_tools", "execute_tool"}:
                continue
            errors.append(
                self._jsonrpc_result(
                    message,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Error: Code Mode meta-tools "
                                    "do not support batch calls"
                                ),
                            }
                        ],
                        "isError": True,
                    },
                )
            )
        return errors

    @staticmethod
    def _jsonrpc_result(
        request: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "jsonrpc": request.get("jsonrpc", "2.0"),
            "id": request.get("id"),
            "result": result,
        }

    async def _call_with_tools_list_transform(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        start_message: Optional[Dict[str, Any]] = None
        body_messages: List[Dict[str, Any]] = []

        async def send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = dict(message)
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            body_messages.append(dict(message))
            if message.get("more_body", False):
                return

            held_start = start_message or {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
            original_body = b"".join(
                part.get("body", b"") for part in body_messages
            )
            content_type = self._content_type(held_start.get("headers", []))
            transformed_body, changed = self._transform_body(
                original_body,
                content_type,
            )

            if not changed:
                await send(held_start)
                for part in body_messages:
                    await send(part)
                return

            transformed_start = dict(held_start)
            transformed_start["headers"] = self._headers_with_content_length(
                list(held_start.get("headers", [])),
                len(transformed_body),
            )
            await send(transformed_start)
            await send(
                {
                    "type": "http.response.body",
                    "body": transformed_body,
                    "more_body": False,
                }
            )

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _content_type(headers: List[tuple[bytes, bytes]]) -> str:
        for key, value in headers:
            if key.lower() == b"content-type":
                return value.decode("latin-1").split(";", 1)[0].strip().lower()
        return ""

    def _transform_body(self, body: bytes, content_type: str) -> tuple[bytes, bool]:
        if content_type == "application/json" or content_type.endswith("+json"):
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return body, False
            if not self._contains_tools_result(data):
                return body, False
            transformed = self._process_response(data)
            return json.dumps(transformed, separators=(",", ":")).encode(), True

        if content_type == "text/event-stream":
            return self._transform_sse_body(body)

        return body, False

    def _transform_sse_body(self, body: bytes) -> tuple[bytes, bool]:
        output = bytearray()
        changed = False
        cursor = 0
        for separator in re.finditer(rb"\r?\n\r?\n", body):
            frame = body[cursor:separator.start()]
            transformed, frame_changed = self._transform_sse_frame(frame)
            output.extend(transformed)
            output.extend(separator.group(0))
            changed = changed or frame_changed
            cursor = separator.end()

        tail, tail_changed = self._transform_sse_frame(body[cursor:])
        output.extend(tail)
        return bytes(output), changed or tail_changed

    def _transform_sse_frame(self, frame: bytes) -> tuple[bytes, bool]:
        if not frame:
            return frame, False

        line_separator = b"\r\n" if b"\r\n" in frame else b"\n"
        lines = frame.split(line_separator)
        data_indexes = [
            index for index, line in enumerate(lines) if line.startswith(b"data:")
        ]
        if not data_indexes:
            return frame, False

        data_parts = []
        for index in data_indexes:
            value = lines[index][5:]
            if value.startswith(b" "):
                value = value[1:]
            data_parts.append(value)

        try:
            data = json.loads(b"\n".join(data_parts))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return frame, False
        if not self._contains_tools_result(data):
            return frame, False

        transformed = self._process_response(data)
        encoded = json.dumps(transformed, separators=(",", ":")).encode()
        first_data_index = data_indexes[0]
        data_index_set = set(data_indexes)
        transformed_lines: List[bytes] = []
        for index, line in enumerate(lines):
            if index == first_data_index:
                transformed_lines.append(b"data: " + encoded)
            elif index not in data_index_set:
                transformed_lines.append(line)
        return line_separator.join(transformed_lines), True

    @staticmethod
    def _contains_tools_result(data: Any) -> bool:
        if isinstance(data, list):
            return any(CodeModeMCPMiddleware._contains_tools_result(item) for item in data)
        if not isinstance(data, dict):
            return False
        result = data.get("result")
        return isinstance(result, dict) and isinstance(result.get("tools"), list)

    def _process_response(self, data: Any) -> Any:
        """Process MCP JSON-RPC response/request messages."""
        if isinstance(data, list):
            return [self._process_single(msg) for msg in data]
        return self._process_single(data)

    def _process_single(self, message: Any) -> Any:
        """Process a single MCP message."""
        if not isinstance(message, dict):
            return message

        # --- tools/list response: replace with code mode tools ---
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            original_tools = message["result"]["tools"]
            # Cache the original tools for the catalog
            self._update_catalog_from_tools(original_tools)
            # Replace with code mode meta-tools
            message["result"]["tools"] = get_code_mode_tool_definitions()
            logger.info(
                "Code mode: replaced %d tools with 2 meta-tools (search_tools, execute_tool)",
                len(original_tools),
            )
            return message

        # --- tools/call response: check if it was for search_tools ---
        # The actual interception of search_tools calls happens via request
        # body inspection. For execute_tool, the middleware rewrites the
        # request before passing upstream (handled in __call__).

        return message

    def _update_catalog_from_tools(self, tools: List[Dict[str, Any]]) -> None:
        """Build/update the internal catalog from tools/list results."""
        # Aggregate FastMCP tools are namespaced. Preserve the exact exposed
        # name so execute_tool can route back without guessing the separator.
        tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            upstream_name = tool.get("name", "")
            if not isinstance(upstream_name, str) or not upstream_name:
                continue
            server_part, tool_part = self._split_upstream_tool_name(tool)
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
        """Handle a search_tools call and return a synthetic MCP result."""
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
                    "text": json.dumps({"tools": results, "total_available": len(visible)}, indent=2),
                }
            ],
            "isError": False,
        }

    def handle_execute_tool(self, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Rewrite an execute_tool call to the actual tool call.

        Returns None if we should let it pass through to upstream (after rewrite),
        or returns a synthetic error result if the tool isn't found.
        """
        if not isinstance(arguments, dict):
            arguments = {}
        tool_qualified = arguments.get("tool", "")
        tool_args = arguments.get("arguments", {})

        if not isinstance(tool_qualified, str) or not tool_qualified:
            return {
                "content": [{"type": "text", "text": "Error: 'tool' parameter is required"}],
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

        # Validate the tool exists in catalog
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
            # Suggest similar tools (disabled entries filtered at serve time)
            suggestions = search_catalog(
                self._catalog,
                tool_qualified,
                limit=3,
                state_manager=self.state_manager,
            )
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n\nDid you mean:\n" + "\n".join(
                    f"  - {s['tool']}: {s['description'][:80]}" for s in suggestions
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Tool '{tool_qualified}' not found in catalog.{suggestion_text}",
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

        # Tool exists — return None to signal "rewrite and pass through"
        return None
