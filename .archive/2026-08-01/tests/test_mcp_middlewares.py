import asyncio
import json
from collections import deque
from typing import Any

import pytest

from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.middleware.mcp_tool_filter import MCPToolFilterMiddleware
from mcpo.services.code_mode import CatalogEntry, search_catalog


class FakeStateManager:
    def __init__(
        self,
        *,
        code_mode: bool = True,
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


class RecordingApp:
    def __init__(
        self,
        response_body: bytes,
        *,
        content_type: bytes = b"application/json",
        response_chunks: list[bytes] | None = None,
    ) -> None:
        self.response_body = response_body
        self.content_type = content_type
        self.response_chunks = response_chunks or [response_body]
        self.calls = 0
        self.received_messages: list[dict[str, Any]] = []
        self.received_body = b""
        self.scope: dict[str, Any] | None = None
        self.sent_messages = self._response_messages()

    def _response_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", self.content_type),
                    (b"content-length", str(len(self.response_body)).encode()),
                    (b"x-upstream", b"preserved"),
                ],
            }
        ]
        for index, chunk in enumerate(self.response_chunks):
            messages.append(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": index < len(self.response_chunks) - 1,
                }
            )
        return messages

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        self.scope = scope
        while True:
            message = await receive()
            self.received_messages.append(message)
            if message["type"] != "http.request":
                break
            self.received_body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        for message in self.sent_messages:
            await send(dict(message))


class ForbiddenApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        raise AssertionError("upstream must not be called")


async def _invoke_asgi(
    app,
    payload: Any,
    *,
    split_request: bool = False,
) -> list[dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    if split_request:
        split_at = max(1, len(body) // 2)
        request_messages = deque(
            [
                {
                    "type": "http.request",
                    "body": body[:split_at],
                    "more_body": True,
                },
                {
                    "type": "http.request",
                    "body": body[split_at:],
                    "more_body": False,
                },
            ]
        )
    else:
        request_messages = deque(
            [{"type": "http.request", "body": body, "more_body": False}]
        )

    async def receive() -> dict[str, Any]:
        if request_messages:
            return request_messages.popleft()
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return sent


def invoke_asgi(
    app,
    payload: Any,
    *,
    split_request: bool = False,
) -> list[dict[str, Any]]:
    return asyncio.run(_invoke_asgi(app, payload, split_request=split_request))


def response_body(messages: list[dict[str, Any]]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


def response_headers(messages: list[dict[str, Any]]) -> dict[bytes, bytes]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    return {key.lower(): value for key, value in start.get("headers", [])}


def decode_mcp_response(messages: list[dict[str, Any]]) -> Any:
    body = response_body(messages)
    content_type = response_headers(messages)[b"content-type"]
    if content_type.startswith(b"text/event-stream"):
        data_lines = [
            line[5:].lstrip()
            for line in body.splitlines()
            if line.startswith(b"data:")
        ]
        body = b"\n".join(data_lines)
    return json.loads(body)


def tools_list_request() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def tools_list_response() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": "alpha_safe",
                    "description": "Safe tool",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "alpha_danger",
                    "description": "Danger tool",
                    "inputSchema": {"type": "object"},
                },
            ]
        },
    }


def encoded_response(content_type: bytes) -> bytes:
    payload = json.dumps(tools_list_response(), separators=(",", ":")).encode()
    if content_type == b"text/event-stream":
        return b"event: message\r\ndata: " + payload + b"\r\n\r\n"
    return payload


def test_code_mode_search_tools_is_synthetic_and_clamps_limit() -> None:
    upstream = ForbiddenApp()
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager()
    middleware._catalog = [
        CatalogEntry("alpha", f"tool_{index}", f"alpha.tool_{index}", "", {})
        for index in range(150)
    ]
    middleware._catalog_built = True

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "search_tools", "arguments": {"query": "", "limit": 1000}},
        },
        split_request=True,
    )

    assert upstream.calls == 0
    payload = decode_mcp_response(sent)
    assert payload["id"] == 7
    result = json.loads(payload["result"]["content"][0]["text"])
    assert len(result["tools"]) == 100
    assert int(response_headers(sent)[b"content-length"]) == len(response_body(sent))


def test_code_mode_execute_tool_rewrites_request_before_upstream() -> None:
    upstream_response = b'{"jsonrpc":"2.0","id":8,"result":{"content":[]}}'
    upstream = RecordingApp(upstream_response)
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"echo": True}}}
    )
    middleware._update_catalog_from_tools(
        [{"name": "alpha_echo", "description": "Echo", "inputSchema": {"type": "object"}}]
    )

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "execute_tool",
                "arguments": {"tool": "alpha.echo", "arguments": {"value": 42}},
            },
        },
        split_request=True,
    )

    assert upstream.calls == 1
    forwarded = json.loads(upstream.received_body)
    assert forwarded["params"] == {"name": "alpha_echo", "arguments": {"value": 42}}
    assert int(dict(upstream.scope["headers"])[b"content-length"]) == len(upstream.received_body)
    assert response_body(sent) == upstream_response


def test_code_mode_rechecks_tool_state_before_rewriting() -> None:
    upstream = ForbiddenApp()
    state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"echo": True}}}
    )
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = state_manager
    middleware._update_catalog_from_tools(
        [{"name": "alpha_echo", "description": "Echo", "inputSchema": {"type": "object"}}]
    )
    state_manager.states["alpha"]["tools"]["echo"] = False

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/call",
            "params": {
                "name": "execute_tool",
                "arguments": {"tool": "alpha.echo", "arguments": {}},
            },
        },
    )

    assert upstream.calls == 0
    payload = decode_mcp_response(sent)
    assert payload["id"] == 81
    assert payload["result"]["isError"] is True
    assert "disabled" in payload["result"]["content"][0]["text"].lower()


def test_code_mode_unknown_execute_tool_returns_synthetic_error() -> None:
    upstream = ForbiddenApp()
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager()
    middleware._catalog_built = True

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "execute_tool",
                "arguments": {"tool": "alpha.missing", "arguments": {}},
            },
        },
    )

    assert upstream.calls == 0
    payload = decode_mcp_response(sent)
    assert payload["id"] == 9
    assert payload["result"]["isError"] is True


def test_code_mode_execute_tool_before_catalog_returns_synthetic_error() -> None:
    upstream = ForbiddenApp()
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager()

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 91,
            "method": "tools/call",
            "params": {
                "name": "execute_tool",
                "arguments": {"tool": "alpha.missing", "arguments": {}},
            },
        },
    )

    assert upstream.calls == 0
    payload = decode_mcp_response(sent)
    assert payload["id"] == 91
    assert payload["result"]["isError"] is True


@pytest.mark.parametrize("content_type", [b"application/json", b"text/event-stream"])
def test_code_mode_transforms_json_and_sse_tools_list(content_type: bytes) -> None:
    body = encoded_response(content_type)
    upstream = RecordingApp(body, content_type=content_type, response_chunks=[body[:11], body[11:]])
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"safe": True, "danger": True}}}
    )

    sent = invoke_asgi(middleware, tools_list_request(), split_request=True)

    payload = decode_mcp_response(sent)
    assert [tool["name"] for tool in payload["result"]["tools"]] == [
        "search_tools",
        "execute_tool",
    ]
    assert [entry.qualified_name for entry in middleware._catalog] == [
        "alpha.safe",
        "alpha.danger",
    ]
    assert int(response_headers(sent)[b"content-length"]) == len(response_body(sent))


def test_filter_blocks_disabled_tool_before_upstream() -> None:
    upstream = ForbiddenApp()
    middleware = MCPToolFilterMiddleware(upstream, server_name="alpha")
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"danger": False}}}
    )

    sent = invoke_asgi(
        middleware,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "danger", "arguments": {}},
        },
        split_request=True,
    )

    assert upstream.calls == 0
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 403
    payload = decode_mcp_response(sent)
    assert payload["error"]["code"] == 403
    assert payload["error"]["data"] == {"tool": "danger", "server": "alpha"}


def test_filter_blocks_disabled_tool_calls_in_batch_before_upstream() -> None:
    upstream = ForbiddenApp()
    middleware = MCPToolFilterMiddleware(upstream, server_name="alpha")
    middleware.state_manager = FakeStateManager(
        states={
            "alpha": {
                "enabled": True,
                "tools": {"danger": False, "destroy": False},
            }
        }
    )

    sent = invoke_asgi(
        middleware,
        [
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "resources/list",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "tools/call",
                "params": {"name": "danger", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 103,
                "method": "tools/call",
                "params": {"name": "destroy", "arguments": {}},
            },
        ],
        split_request=True,
    )

    assert upstream.calls == 0
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 403
    payload = decode_mcp_response(sent)
    assert [error["id"] for error in payload] == [102, 103]
    assert all(error["error"]["code"] == 403 for error in payload)


def test_code_mode_rejects_meta_tool_calls_in_batch_before_upstream() -> None:
    upstream = ForbiddenApp()
    middleware = CodeModeMCPMiddleware(upstream)
    middleware.state_manager = FakeStateManager()

    sent = invoke_asgi(
        middleware,
        [
            {
                "jsonrpc": "2.0",
                "id": 111,
                "method": "tools/call",
                "params": {
                    "name": "search_tools",
                    "arguments": {"query": "search"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 112,
                "method": "tools/call",
                "params": {
                    "name": "execute_tool",
                    "arguments": {"tool": "alpha.echo", "arguments": {}},
                },
            },
        ],
        split_request=True,
    )

    assert upstream.calls == 0
    payload = decode_mcp_response(sent)
    assert [result["id"] for result in payload] == [111, 112]
    assert all(result["result"]["isError"] is True for result in payload)
    assert all(
        "do not support batch calls"
        in result["result"]["content"][0]["text"]
        for result in payload
    )


@pytest.mark.parametrize("content_type", [b"application/json", b"text/event-stream"])
def test_filter_transforms_json_and_sse_tools_list(content_type: bytes) -> None:
    body = encoded_response(content_type)
    upstream = RecordingApp(body, content_type=content_type, response_chunks=[body[:13], body[13:]])
    middleware = MCPToolFilterMiddleware(upstream)
    middleware.state_manager = FakeStateManager(
        states={"alpha": {"enabled": True, "tools": {"safe": True, "danger": False}}}
    )

    sent = invoke_asgi(middleware, tools_list_request(), split_request=True)

    payload = decode_mcp_response(sent)
    assert [tool["name"] for tool in payload["result"]["tools"]] == ["alpha_safe"]
    assert int(response_headers(sent)[b"content-length"]) == len(response_body(sent))
    assert response_headers(sent)[b"x-upstream"] == b"preserved"


@pytest.mark.parametrize("middleware_type", [CodeModeMCPMiddleware, MCPToolFilterMiddleware])
def test_middlewares_pass_unrelated_request_and_response_unchanged(middleware_type) -> None:
    response = b'{ "jsonrpc": "2.0", "id": 11, "result": {"resources": []} }'
    chunks = [response[:9], response[9:31], response[31:]]
    upstream = RecordingApp(response, response_chunks=chunks)
    middleware = middleware_type(upstream)
    state_manager = FakeStateManager()
    middleware.state_manager = state_manager
    request = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "resources/list",
        "params": {"cursor": "next"},
    }

    sent = invoke_asgi(middleware, request, split_request=True)

    assert upstream.calls == 1
    assert json.loads(upstream.received_body) == request
    assert len(upstream.received_messages) == 2
    assert sent == upstream.sent_messages
    assert state_manager.refresh_calls == 1


@pytest.mark.parametrize("middleware_type", [CodeModeMCPMiddleware, MCPToolFilterMiddleware])
def test_middlewares_pass_unrelated_batches_unchanged(middleware_type) -> None:
    response = (
        b'[ { "jsonrpc": "2.0", "id": 121, "result": {"resources": []} },'
        b'{"jsonrpc":"2.0","id":122,"result":{"prompts":[]}} ]'
    )
    upstream = RecordingApp(response, response_chunks=[response[:17], response[17:]])
    middleware = middleware_type(upstream)
    middleware.state_manager = FakeStateManager()
    request = [
        {
            "jsonrpc": "2.0",
            "id": 121,
            "method": "resources/list",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": 122,
            "method": "prompts/list",
            "params": {},
        },
    ]

    sent = invoke_asgi(middleware, request, split_request=True)

    assert upstream.calls == 1
    assert json.loads(upstream.received_body) == request
    assert sent == upstream.sent_messages


def test_search_catalog_validates_and_clamps_limit() -> None:
    catalog = [
        CatalogEntry("alpha", f"tool_{index}", f"alpha.tool_{index}", "", {})
        for index in range(150)
    ]

    assert len(search_catalog(catalog, "", limit=-5)) == 1
    assert len(search_catalog(catalog, "", limit=0)) == 1
    assert len(search_catalog(catalog, "", limit=1000)) == 100
    assert len(search_catalog(catalog, "", limit="invalid")) == 10  # type: ignore[arg-type]
