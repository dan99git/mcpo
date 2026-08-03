"""
MCP Tool Filtering Middleware

Intercepts MCP protocol messages to filter tools based on mcpo_state.json.
This ensures tool enable/disable toggles work on both FastMCP proxy ports.
"""

import base64
import importlib.metadata
import json
import logging
import re
from typing import Any, Dict, List, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from mcpo.services.state import get_state_manager
from mcpo.utils.main import SUPPORTED_MCP_VERSIONS

logger = logging.getLogger(__name__)

# MCP 2026-07-28 (SEP-2549): cache freshness hints attached to proxied
# tools/list results. Private because tool visibility depends on per-instance
# enable/disable state, so shared intermediaries must not cache it.
TOOLS_LIST_TTL_MS = 60000
TOOLS_LIST_CACHE_SCOPE = "private"

# MCP 2026-07-28 (SEP-2575): per-request _meta keys.
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"

# MCP 2026-07-28 spec-reserved JSON-RPC error codes (-32020..-32099).
HEADER_MISMATCH_ERROR_CODE = -32020
UNSUPPORTED_PROTOCOL_VERSION_ERROR_CODE = -32022

# MCP 2026-07-28 (SEP-2243): methods whose Mcp-Name header mirrors a body field.
_MCP_NAME_SOURCE_FIELDS = {
    "tools/call": "name",
    "prompts/get": "name",
    "resources/read": "uri",
}


def _mcpo_version() -> str:
    try:
        return importlib.metadata.version("mcpo")
    except Exception:
        return "unknown"


class MCPToolFilterMiddleware:
    """
    ASGI middleware that filters MCP tools based on StateManager.
    
    Intercepts:
    - tools/list responses → filters disabled tools
    - tools/call requests → blocks disabled tools with 403
    """
    
    def __init__(self, app: ASGIApp, server_name: str = None):
        self.app = app
        self.server_name = server_name  # None = aggregate proxy (multi-server)
        self.state_manager = get_state_manager()
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI interface - intercept and filter MCP messages."""
        
        if scope["type"] != "http":
            # Only process HTTP requests
            await self.app(scope, receive, send)
            return

        self.state_manager.refresh_if_changed()
        
        request_messages, request_body = await self._read_request(receive)
        request_data: Any = None
        try:
            request_data = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # MCP 2026-07-28 surface checks (backward compatible: only fire when
        # the request explicitly carries 2026-era headers/_meta/methods).
        spec_error = self._spec_2026_error(scope, request_data)
        if spec_error is not None:
            await self._send_json_response(send, spec_error, status=400)
            return

        if (
            isinstance(request_data, dict)
            and request_data.get("method") == "server/discover"
        ):
            await self._send_json_response(
                send,
                self._discover_response(request_data),
                status=200,
            )
            return

        if isinstance(request_data, list):
            blocked_responses = [
                blocked
                for message in request_data
                if isinstance(message, dict)
                and message.get("method") == "tools/call"
                if (blocked := self._blocked_tool_call(message)) is not None
            ]
            if blocked_responses:
                await self._send_json_response(
                    send,
                    blocked_responses,
                    status=403,
                )
                return

        if isinstance(request_data, dict) and request_data.get("method") == "tools/call":
            blocked_response = self._blocked_tool_call(request_data)
            if blocked_response is not None:
                await self._send_json_response(send, blocked_response, status=403)
                return

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
    async def _send_json_response(
        cls,
        send: Send,
        payload: Any,
        status: int,
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

    def _spec_2026_error(self, scope: Scope, request_data: Any) -> Optional[Dict[str, Any]]:
        """Return a JSON-RPC error for MCP 2026-07-28 protocol violations, else None."""
        if isinstance(request_data, dict):
            return (
                self._protocol_version_error(request_data)
                or self._header_mismatch_error(scope, request_data)
            )
        if isinstance(request_data, list):
            # Mcp-Method/Mcp-Name headers cannot describe a batch; only the
            # per-message _meta protocol version is checked here.
            for message in request_data:
                if not isinstance(message, dict):
                    continue
                error_response = self._protocol_version_error(message)
                if error_response is not None:
                    return error_response
        return None

    @staticmethod
    def _protocol_version_error(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """SEP-2575: reject requests declaring an unsupported protocol version."""
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            return None
        requested = meta.get(PROTOCOL_VERSION_META_KEY)
        if requested is None or requested in SUPPORTED_MCP_VERSIONS:
            return None
        logger.warning("Rejected request with unsupported protocol version: %s", requested)
        return {
            "jsonrpc": message.get("jsonrpc", "2.0"),
            "id": message.get("id"),
            "error": {
                "code": UNSUPPORTED_PROTOCOL_VERSION_ERROR_CODE,
                "message": "Unsupported protocol version",
                "data": {
                    "supported": list(SUPPORTED_MCP_VERSIONS),
                    "requested": requested,
                },
            },
        }

    def _header_mismatch_error(
        self,
        scope: Scope,
        message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """SEP-2243: validate Mcp-Method/Mcp-Name headers against the body.

        Headers absent = legacy (pre-2026-07-28) client; no validation.
        """
        header_method = self._scope_header(scope, b"mcp-method")
        header_name = self._scope_header(scope, b"mcp-name")
        if header_method is None and header_name is None:
            return None

        body_method = message.get("method")
        if header_method is not None and header_method != body_method:
            return self._header_mismatch_response(
                message,
                f"Header mismatch: Mcp-Method header value '{header_method}' "
                f"does not match body value '{body_method}'",
            )

        if header_name is not None:
            body_field = _MCP_NAME_SOURCE_FIELDS.get(body_method)
            if body_field is not None:
                params = message.get("params")
                body_name = params.get(body_field) if isinstance(params, dict) else None
                if self._decode_header_value(header_name) != body_name:
                    return self._header_mismatch_response(
                        message,
                        f"Header mismatch: Mcp-Name header value '{header_name}' "
                        f"does not match body value '{body_name}'",
                    )
        return None

    @staticmethod
    def _header_mismatch_response(
        message: Dict[str, Any],
        error_message: str,
    ) -> Dict[str, Any]:
        logger.warning(error_message)
        return {
            "jsonrpc": message.get("jsonrpc", "2.0"),
            "id": message.get("id"),
            "error": {
                "code": HEADER_MISMATCH_ERROR_CODE,
                "message": error_message,
            },
        }

    @staticmethod
    def _scope_header(scope: Scope, name: bytes) -> Optional[str]:
        for key, value in scope.get("headers") or []:
            if key.lower() == name:
                return value.decode("latin-1")
        return None

    @staticmethod
    def _decode_header_value(value: str) -> str:
        """Decode the MCP Base64 sentinel format (=?base64?...?=) if present."""
        prefix, suffix = "=?base64?", "?="
        if value.startswith(prefix) and value.endswith(suffix):
            try:
                return base64.b64decode(
                    value[len(prefix):-len(suffix)]
                ).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                # Malformed sentinel: compare raw so it surfaces as a mismatch.
                return value
        return value

    def _discover_response(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """SEP-2575: answer server/discover directly; backends predate it."""
        return {
            "jsonrpc": message.get("jsonrpc", "2.0"),
            "id": message.get("id"),
            "result": {
                "resultType": "complete",
                "supportedVersions": list(SUPPORTED_MCP_VERSIONS),
                "capabilities": {"tools": {"listChanged": False}},
                "ttlMs": TOOLS_LIST_TTL_MS,
                "cacheScope": TOOLS_LIST_CACHE_SCOPE,
                "_meta": {
                    SERVER_INFO_META_KEY: {
                        "name": "mcpo",
                        "title": "mcpo MCP proxy",
                        "version": _mcpo_version(),
                    }
                },
            },
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
            transformed = self._filter_mcp_message(data)
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

        transformed = self._filter_mcp_message(data)
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
            return any(MCPToolFilterMiddleware._contains_tools_result(item) for item in data)
        if not isinstance(data, dict):
            return False
        result = data.get("result")
        return isinstance(result, dict) and isinstance(result.get("tools"), list)
    
    def _filter_mcp_message(self, data: Any) -> Any:
        """Filter MCP protocol messages based on tool enabled state."""
        
        # Handle JSON-RPC batch
        if isinstance(data, list):
            return [self._filter_single_message(msg) for msg in data]
        
        return self._filter_single_message(data)
    
    def _filter_single_message(self, message: Any) -> Any:
        """Filter a single MCP message."""
        if not isinstance(message, dict):
            return message
        
        # Check if this is a tools/list response
        result = message.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            tools = message["result"]["tools"]
            filtered_tools = []
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                tool_name = tool.get("name", "")
                if not isinstance(tool_name, str):
                    continue
                annotations = tool.get("annotations")
                server_name, state_tool_name = self._resolve_tool_name(
                    tool_name,
                    annotations if isinstance(annotations, dict) else None,
                )
                if self._is_tool_available(server_name, state_tool_name):
                    filtered_tools.append(tool)

            # MCP 2026-07-28: deterministic tool order (SEP minor change 3).
            filtered_tools.sort(key=lambda item: item.get("name", ""))

            scope_name = self.server_name or "aggregate proxy"
            logger.info(
                "Filtered tools for '%s': %d → %d tools",
                scope_name,
                len(tools),
                len(filtered_tools),
            )
            
            message["result"]["tools"] = filtered_tools
            # MCP 2026-07-28 (SEP-2549): cache hints on list results. setdefault
            # so a backend that already speaks 2026-07-28 keeps its own hints.
            message["result"].setdefault("ttlMs", TOOLS_LIST_TTL_MS)
            message["result"].setdefault("cacheScope", TOOLS_LIST_CACHE_SCOPE)
            return message
        
        # Check if this is a tools/call request (shouldn't happen in response, but defensive)
        if message.get("method") == "tools/call":
            blocked_response = self._blocked_tool_call(message)
            if blocked_response is not None:
                return blocked_response
        
        return message

    def _blocked_tool_call(
        self,
        message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        params = message.get("params")
        if not isinstance(params, dict):
            return None
        tool_name = params.get("name", "")
        if not isinstance(tool_name, str) or not tool_name:
            return None

        server_name, state_tool_name = self._resolve_tool_name(tool_name)
        if self._is_tool_available(server_name, state_tool_name):
            return None

        logger.warning("Blocked call to disabled tool: %s/%s", server_name, tool_name)
        return {
            "jsonrpc": message.get("jsonrpc", "2.0"),
            "id": message.get("id"),
            "error": {
                "code": 403,
                "message": f"Tool '{tool_name}' is disabled",
                "data": {"tool": tool_name, "server": server_name},
            },
        }

    def _is_tool_available(self, server_name: str, tool_name: str) -> bool:
        if not server_name:
            return True
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
        """Find which server a tool belongs to by checking state."""
        server_name, _ = self._resolve_tool_name(tool_name)
        return server_name
