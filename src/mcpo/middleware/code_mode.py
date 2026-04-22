"""
Code Mode MCP Middleware

When code mode is enabled, this middleware intercepts MCP protocol messages on the
FastMCP proxy (port 8001) to:

1. Replace tools/list responses with only search_tools + execute_tool
2. Intercept tools/call for search_tools → synthesize a response from the catalog
3. Intercept tools/call for execute_tool → rewrite the request to the actual
   aggregate-format tool call before passing upstream
4. Pass through all other MCP messages unchanged

This sits as ASGI middleware on the FastMCP proxy app, similar to MCPToolFilterMiddleware.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from starlette.types import ASGIApp, Receive, Scope, Send

from mcpo.services.code_mode import (
    CatalogEntry,
    build_catalog,
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
      - tools/call search_tools → searches the internal catalog, returns results
        without hitting upstream
      - tools/call execute_tool → validates the qualified name, rewrites the
        request to the aggregate "server__tool" form, and forwards upstream
    """

    def __init__(self, app: ASGIApp, server_name: Optional[str] = None):
        self.app = app
        self.server_name = server_name  # None = aggregate proxy
        self.state_manager = get_state_manager()
        self._catalog: List[CatalogEntry] = []
        self._catalog_built = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.state_manager.is_code_mode_enabled():
            await self.app(scope, receive, send)
            return

        # Drain the full request body up front so we can inspect it.
        full_body, tail_messages = await _drain_body(receive)

        synthesized_result: Optional[Dict[str, Any]] = None
        rewritten_body: Optional[bytes] = None
        request_id: Any = None

        try:
            if full_body:
                parsed = json.loads(full_body)
                if isinstance(parsed, dict) and parsed.get("method") == "tools/call":
                    params = parsed.get("params", {}) or {}
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {}) or {}
                    request_id = parsed.get("id")

                    if tool_name == "search_tools":
                        synthesized_result = self.handle_search_tools(arguments)
                    elif tool_name == "execute_tool":
                        decision = self.handle_execute_tool(arguments)
                        if decision is not None:
                            synthesized_result = decision
                        else:
                            rewritten_body = _rewrite_execute_tool(parsed, arguments)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Code mode: request body not JSON-RPC, pass-through: %s", exc)

        # Short-circuit: return a synthetic JSON-RPC response without calling upstream.
        if synthesized_result is not None:
            await _send_jsonrpc_response(send, request_id, synthesized_result)
            return

        # Replay the (possibly rewritten) body to the downstream app.
        body_for_downstream = rewritten_body if rewritten_body is not None else full_body
        receive_replay = _make_replay_receive(body_for_downstream, tail_messages)

        # Response-side filtering: rewrite tools/list responses to the 2 meta-tools.
        response_body_parts: List[bytes] = []
        start_message_held: Optional[Dict[str, Any]] = None
        is_streaming = False

        async def send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal start_message_held, is_streaming

            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                content_type = headers.get(b"content-type", b"").decode("utf-8", errors="ignore").lower()

                if "text/event-stream" in content_type or "stream" in content_type:
                    is_streaming = True
                    await send(message)
                    return

                start_message_held = message
                return

            if message["type"] == "http.response.body":
                if is_streaming:
                    await send(message)
                    return

                body = message.get("body", b"")
                if body:
                    response_body_parts.append(body)

                if not message.get("more_body", False):
                    full_resp = b"".join(response_body_parts)
                    try:
                        data = json.loads(full_resp)
                        filtered = self._process_response(data)
                        out_body = json.dumps(filtered).encode()
                        status = start_message_held.get("status", 200) if start_message_held else 200
                        headers_out = (start_message_held or {}).get(
                            "headers", [(b"content-type", b"application/json")]
                        )
                        await send({"type": "http.response.start", "status": status, "headers": headers_out})
                        await send({"type": "http.response.body", "body": out_body})
                        return
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.debug("Code mode: not filtering response: %s", exc)
                        if start_message_held:
                            await send(start_message_held)
                        else:
                            await send({"type": "http.response.start", "status": 200, "headers": []})
                        await send({"type": "http.response.body", "body": full_resp})
                        return
                return

            await send(message)

        await self.app(scope, receive_replay, send_wrapper)

    # ------------------------------------------------------------------
    # Response-side transformations (tools/list → meta-tools)
    # ------------------------------------------------------------------

    def _process_response(self, data: Any) -> Any:
        if isinstance(data, list):
            return [self._process_single(msg) for msg in data]
        return self._process_single(data)

    def _process_single(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if "result" in message and "tools" in message.get("result", {}):
            original_tools = message["result"]["tools"]
            self._update_catalog_from_tools(original_tools)
            message["result"]["tools"] = get_code_mode_tool_definitions()
            logger.info(
                "Code mode: replaced %d tools with 2 meta-tools (search_tools, execute_tool)",
                len(original_tools),
            )
        return message

    def _update_catalog_from_tools(self, tools: List[Dict[str, Any]]) -> None:
        """Build/update the internal catalog from tools/list results."""
        tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
        for tool in tools:
            name = tool.get("name", "")
            if "__" in name:
                server_part, tool_part = name.split("__", 1)
                tool_copy = dict(tool)
                tool_copy["name"] = tool_part
                tools_by_server.setdefault(server_part, []).append(tool_copy)
            elif self.server_name:
                tools_by_server.setdefault(self.server_name, []).append(tool)
            else:
                server = tool.get("annotations", {}).get("server", "unknown")
                tools_by_server.setdefault(server, []).append(tool)

        self._catalog = build_catalog(tools_by_server)
        self._catalog_built = True
        logger.info("Code mode: built catalog with %d tools", len(self._catalog))

    # ------------------------------------------------------------------
    # Request-side helpers (search_tools synth, execute_tool rewrite)
    # ------------------------------------------------------------------

    def handle_search_tools(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Return a synthetic MCP tools/call result for a search_tools query."""
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        results = search_catalog(self._catalog, query, limit=limit)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"tools": results, "total_available": len(self._catalog)},
                        indent=2,
                    ),
                }
            ],
            "isError": False,
        }

    def handle_execute_tool(self, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate an execute_tool request.

        Returns:
            None — when the tool is known and the request should be rewritten
                   and forwarded upstream.
            dict — when a synthetic error result should be returned instead
                   (missing field, unknown tool, etc.).
        """
        tool_qualified = arguments.get("tool", "")
        if not tool_qualified:
            return {
                "content": [{"type": "text", "text": "Error: 'tool' parameter is required"}],
                "isError": True,
            }

        # If the catalog has been built (first tools/list came back), validate
        # the qualified name. When the catalog is empty (no tools/list yet),
        # we trust the caller and let it through so bootstrap flows work.
        if self._catalog_built:
            found = any(entry.qualified_name == tool_qualified for entry in self._catalog)
            if not found:
                suggestions = search_catalog(self._catalog, tool_qualified, limit=3)
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

        return None


# ---------------------------------------------------------------------------
# ASGI helpers (free functions so they can be unit-tested without state)
# ---------------------------------------------------------------------------

async def _drain_body(receive: Receive) -> "tuple[bytes, List[Dict[str, Any]]]":
    """Read the full request body; return it plus any trailing messages (e.g. http.disconnect)."""
    parts: List[bytes] = []
    tail: List[Dict[str, Any]] = []
    more = True
    while more:
        msg = await receive()
        if msg["type"] == "http.request":
            parts.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        else:
            tail.append(msg)
            more = False
    return b"".join(parts), tail


def _make_replay_receive(body: bytes, tail: List[Dict[str, Any]]) -> Receive:
    """Build a receive callable that yields the given body once, then tail messages, then disconnect."""
    state = {"sent_body": False, "tail": list(tail)}

    async def _receive() -> Dict[str, Any]:
        if not state["sent_body"]:
            state["sent_body"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        if state["tail"]:
            return state["tail"].pop(0)
        return {"type": "http.disconnect"}

    return _receive


def _rewrite_execute_tool(parsed: Dict[str, Any], arguments: Dict[str, Any]) -> bytes:
    """Rewrite an execute_tool request into a direct tools/call for the qualified tool.

    'server.tool_name' → 'server__tool_name' (aggregate proxy naming convention).
    """
    qualified = arguments.get("tool", "")
    if "." in qualified:
        server, tool = qualified.split(".", 1)
        aggregate_name = f"{server}__{tool}"
    else:
        aggregate_name = qualified

    rewritten = dict(parsed)
    rewritten["params"] = {
        "name": aggregate_name,
        "arguments": arguments.get("arguments", {}) or {},
    }
    return json.dumps(rewritten).encode()


async def _send_jsonrpc_response(send: Send, request_id: Any, result: Dict[str, Any]) -> None:
    """Send a synthesized JSON-RPC 2.0 response over ASGI."""
    payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})
