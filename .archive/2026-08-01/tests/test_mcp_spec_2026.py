"""MCP 2026-07-28 spec surface features in the proxy middleware.

Covers server/discover, Mcp-Method/Mcp-Name header validation (SEP-2243),
tools/list cache hints + deterministic order (SEP-2549), per-request _meta
protocol version rejection (SEP-2575), and REST-toggle independence.
"""

import asyncio
import base64
import json
from collections import deque
from typing import Any

from mcpo.middleware.mcp_tool_filter import (
    HEADER_MISMATCH_ERROR_CODE,
    MCPToolFilterMiddleware,
    TOOLS_LIST_CACHE_SCOPE,
    TOOLS_LIST_TTL_MS,
    UNSUPPORTED_PROTOCOL_VERSION_ERROR_CODE,
)
from mcpo.services.state import StateManager
from mcpo.utils.main import SUPPORTED_MCP_VERSIONS


class FakeStateManager:
    def __init__(self, states: dict[str, dict[str, Any]] | None = None) -> None:
        self.states = states or {}

    def refresh_if_changed(self) -> bool:
        return False

    def is_server_enabled(self, server_name: str) -> bool:
        return self.states.get(server_name, {}).get("enabled", True)

    def is_tool_enabled(self, server_name: str, tool_name: str) -> bool:
        return self.states.get(server_name, {}).get("tools", {}).get(tool_name, True)

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        return self.states


class RecordingApp:
    def __init__(self, response_payload: Any) -> None:
        self.response_body = json.dumps(
            response_payload, separators=(",", ":")
        ).encode()
        self.calls = 0

    async def __call__(self, scope, receive, send) -> None:
        self.calls += 1
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(self.response_body)).encode()),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": self.response_body,
                "more_body": False,
            }
        )


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
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
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

    scope_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    for key, value in (headers or {}).items():
        scope_headers.append((key.lower().encode(), value.encode()))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": scope_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return sent


def invoke_asgi(
    app,
    payload: Any,
    *,
    headers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return asyncio.run(_invoke_asgi(app, payload, headers=headers))


def response_status(messages: list[dict[str, Any]]) -> int:
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    return start["status"]


def response_json(messages: list[dict[str, Any]]) -> Any:
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return json.loads(body)


def tools_list_request(meta: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if meta is not None:
        params["_meta"] = meta
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params}


def tools_call_request(tool_name: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": {}},
    }


def tools_list_response(tool_names: list[str]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": name,
                    "description": f"Tool {name}",
                    "inputSchema": {"type": "object"},
                }
                for name in tool_names
            ]
        },
    }


def call_result_response() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
    }


def make_filter(upstream, server_name: str | None = "alpha") -> MCPToolFilterMiddleware:
    middleware = MCPToolFilterMiddleware(upstream, server_name=server_name)
    middleware.state_manager = FakeStateManager()
    return middleware


def test_server_discover_is_answered_by_the_middleware() -> None:
    upstream = ForbiddenApp()
    middleware = make_filter(upstream)

    payload = {"jsonrpc": "2.0", "id": "discover-1", "method": "server/discover"}
    messages = invoke_asgi(middleware, payload)

    assert upstream.calls == 0
    assert response_status(messages) == 200
    data = response_json(messages)
    assert data["id"] == "discover-1"
    result = data["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == SUPPORTED_MCP_VERSIONS
    assert "2026-07-28" in result["supportedVersions"]
    assert "2025-06-18" in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    assert result["ttlMs"] == TOOLS_LIST_TTL_MS
    assert result["cacheScope"] == TOOLS_LIST_CACHE_SCOPE
    server_info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert server_info["name"] == "mcpo"
    assert "version" in server_info


def test_mcp_method_header_mismatch_returns_32020() -> None:
    upstream = ForbiddenApp()
    middleware = make_filter(upstream)

    messages = invoke_asgi(
        middleware,
        tools_call_request("alpha_safe"),
        headers={"Mcp-Method": "tools/list"},
    )

    assert upstream.calls == 0
    assert response_status(messages) == 400
    data = response_json(messages)
    assert data["error"]["code"] == HEADER_MISMATCH_ERROR_CODE
    assert data["id"] == 7


def test_mcp_name_header_mismatch_returns_32020() -> None:
    upstream = ForbiddenApp()
    middleware = make_filter(upstream)

    messages = invoke_asgi(
        middleware,
        tools_call_request("alpha_safe"),
        headers={"Mcp-Method": "tools/call", "Mcp-Name": "other_tool"},
    )

    assert upstream.calls == 0
    assert response_status(messages) == 400
    data = response_json(messages)
    assert data["error"]["code"] == HEADER_MISMATCH_ERROR_CODE


def test_matching_headers_pass_through_to_upstream() -> None:
    upstream = RecordingApp(call_result_response())
    middleware = make_filter(upstream)

    messages = invoke_asgi(
        middleware,
        tools_call_request("alpha_safe"),
        headers={"Mcp-Method": "tools/call", "Mcp-Name": "alpha_safe"},
    )

    assert upstream.calls == 1
    assert response_status(messages) == 200
    data = response_json(messages)
    assert data["result"]["isError"] is False


def test_base64_sentinel_mcp_name_matches_body() -> None:
    upstream = RecordingApp(call_result_response())
    middleware = make_filter(upstream)

    encoded = base64.b64encode("alpha_safe".encode()).decode()
    messages = invoke_asgi(
        middleware,
        tools_call_request("alpha_safe"),
        headers={
            "Mcp-Method": "tools/call",
            "Mcp-Name": f"=?base64?{encoded}?=",
        },
    )

    assert upstream.calls == 1
    assert response_status(messages) == 200


def test_absent_headers_pass_through_unchanged() -> None:
    upstream = RecordingApp(call_result_response())
    middleware = make_filter(upstream)

    messages = invoke_asgi(middleware, tools_call_request("alpha_safe"))

    assert upstream.calls == 1
    assert response_status(messages) == 200
    data = response_json(messages)
    assert data["result"]["isError"] is False


def test_tools_list_sorted_with_cache_hints() -> None:
    upstream = RecordingApp(tools_list_response(["zeta", "alpha", "mid"]))
    middleware = make_filter(upstream)

    messages = invoke_asgi(middleware, tools_list_request())

    assert response_status(messages) == 200
    result = response_json(messages)["result"]
    assert [tool["name"] for tool in result["tools"]] == ["alpha", "mid", "zeta"]
    assert result["ttlMs"] == TOOLS_LIST_TTL_MS
    assert result["cacheScope"] == TOOLS_LIST_CACHE_SCOPE


def test_unsupported_meta_protocol_version_returns_32022() -> None:
    upstream = ForbiddenApp()
    middleware = make_filter(upstream)

    meta = {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    messages = invoke_asgi(middleware, tools_list_request(meta))

    assert upstream.calls == 0
    assert response_status(messages) == 400
    data = response_json(messages)
    assert data["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION_ERROR_CODE
    assert data["error"]["data"]["supported"] == SUPPORTED_MCP_VERSIONS
    assert data["error"]["data"]["requested"] == "1900-01-01"


def test_supported_meta_protocol_version_passes_through() -> None:
    upstream = RecordingApp(tools_list_response(["alpha"]))
    middleware = make_filter(upstream)

    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    messages = invoke_asgi(middleware, tools_list_request(meta))

    assert upstream.calls == 1
    assert response_status(messages) == 200
    result = response_json(messages)["result"]
    assert [tool["name"] for tool in result["tools"]] == ["alpha"]


def test_rest_tools_toggle_does_not_affect_mcp_tools_list(tmp_path) -> None:
    """Regression guard: the global REST tools toggle (port-8000 REST surface)
    must not change what the MCP proxy's tools/list returns."""

    def run_with_toggle(enabled: bool) -> list[str]:
        state = StateManager(state_file_path=str(tmp_path / f"state_{enabled}.json"))
        state.set_rest_tools_enabled(enabled)
        assert state.is_rest_tools_enabled() is enabled
        upstream = RecordingApp(tools_list_response(["beta", "alpha"]))
        middleware = MCPToolFilterMiddleware(upstream, server_name="alpha")
        middleware.state_manager = state
        messages = invoke_asgi(middleware, tools_list_request())
        assert response_status(messages) == 200
        return [tool["name"] for tool in response_json(messages)["result"]["tools"]]

    assert run_with_toggle(True) == run_with_toggle(False) == ["alpha", "beta"]
