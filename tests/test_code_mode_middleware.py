"""
Unit tests for the request-side interception in CodeModeMCPMiddleware.

Covers the three flows that were stubbed out before:
1. tools/call for search_tools → synthesized response (no upstream hit)
2. tools/call for execute_tool with valid qualified name → rewrites to
   "server__tool" aggregate form and forwards
3. tools/call for execute_tool with unknown tool → synthesized error response
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.services.code_mode import CatalogEntry
from mcpo.services.state import get_state_manager


class _UpstreamSpy:
    """ASGI app that records the request body it receives and returns a fixed JSON-RPC result."""

    def __init__(self, response: Dict[str, Any]) -> None:
        self.response = response
        self.received_body: bytes = b""
        self.called: bool = False

    async def __call__(self, scope, receive, send):
        self.called = True
        # Drain the request
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                self.received_body += msg.get("body", b"")
                more = msg.get("more_body", False)
            else:
                more = False
        body = json.dumps(self.response).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})


async def _invoke(middleware, request_body: bytes):
    """Drive the middleware with one http.request message and collect the response."""
    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    sent_body = {"called": False}
    captured: List[Dict[str, Any]] = []

    async def receive():
        if not sent_body["called"]:
            sent_body["called"] = True
            return {"type": "http.request", "body": request_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        captured.append(msg)

    await middleware(scope, receive, send)
    start = next((m for m in captured if m["type"] == "http.response.start"), None)
    body_parts = [m["body"] for m in captured if m["type"] == "http.response.body"]
    return start, b"".join(body_parts)


@pytest.fixture
def code_mode_on():
    sm = get_state_manager()
    prev = sm.is_code_mode_enabled()
    sm.set_code_mode_enabled(True)
    yield
    sm.set_code_mode_enabled(prev)


def _seed_catalog(middleware):
    """Populate the middleware's catalog directly, as a tools/list response would."""
    middleware._catalog = [
        CatalogEntry(
            server_name="time",
            tool_name="get_current_time",
            qualified_name="time.get_current_time",
            description="Return the current time in a timezone",
            input_schema={"type": "object", "properties": {"timezone": {"type": "string"}}},
            tags=["time", "current", "timezone"],
        ),
        CatalogEntry(
            server_name="brave-search",
            tool_name="web_search",
            qualified_name="brave-search.web_search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            tags=["search", "web", "brave"],
        ),
    ]
    middleware._catalog_built = True


@pytest.mark.asyncio
async def test_search_tools_is_synthesized_without_upstream(code_mode_on):
    upstream = _UpstreamSpy(response={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
    mw = CodeModeMCPMiddleware(upstream)
    _seed_catalog(mw)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": "search_tools", "arguments": {"query": "time"}},
    }).encode()

    start, resp_body = await _invoke(mw, body)

    assert start is not None
    assert start["status"] == 200
    assert upstream.called is False, "search_tools must not hit upstream"

    payload = json.loads(resp_body)
    assert payload["id"] == 42
    assert payload["jsonrpc"] == "2.0"
    # The synthesized result includes a JSON-encoded text block with tool entries
    text = payload["result"]["content"][0]["text"]
    inner = json.loads(text)
    assert any(t["tool"] == "time.get_current_time" for t in inner["tools"])


@pytest.mark.asyncio
async def test_execute_tool_rewrites_to_aggregate_name(code_mode_on):
    upstream = _UpstreamSpy(response={"jsonrpc": "2.0", "id": 7, "result": {"content": []}})
    mw = CodeModeMCPMiddleware(upstream)
    _seed_catalog(mw)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "execute_tool",
            "arguments": {"tool": "time.get_current_time", "arguments": {"timezone": "UTC"}},
        },
    }).encode()

    start, resp_body = await _invoke(mw, body)

    assert start is not None
    assert upstream.called is True, "execute_tool with valid name must forward upstream"

    forwarded = json.loads(upstream.received_body)
    assert forwarded["method"] == "tools/call"
    assert forwarded["params"]["name"] == "time__get_current_time"
    assert forwarded["params"]["arguments"] == {"timezone": "UTC"}


@pytest.mark.asyncio
async def test_execute_tool_unknown_returns_synthesized_error(code_mode_on):
    upstream = _UpstreamSpy(response={"jsonrpc": "2.0", "id": 9, "result": {}})
    mw = CodeModeMCPMiddleware(upstream)
    _seed_catalog(mw)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "execute_tool", "arguments": {"tool": "bogus.nope"}},
    }).encode()

    start, resp_body = await _invoke(mw, body)

    assert start is not None
    assert upstream.called is False, "unknown execute_tool must not reach upstream"

    payload = json.loads(resp_body)
    assert payload["id"] == 9
    assert payload["result"]["isError"] is True
    assert "not found in catalog" in payload["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_unrelated_request_passes_through(code_mode_on):
    upstream = _UpstreamSpy(response={"jsonrpc": "2.0", "id": 3, "result": {"ok": True}})
    mw = CodeModeMCPMiddleware(upstream)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "initialize",
        "params": {},
    }).encode()

    start, resp_body = await _invoke(mw, body)

    assert upstream.called is True
    assert upstream.received_body == body
    payload = json.loads(resp_body)
    assert payload["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_middleware_noop_when_code_mode_disabled():
    # State manager default state — do NOT enable code mode
    sm = get_state_manager()
    sm.set_code_mode_enabled(False)

    upstream = _UpstreamSpy(response={"jsonrpc": "2.0", "id": 1, "result": {}})
    mw = CodeModeMCPMiddleware(upstream)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search_tools", "arguments": {}},
    }).encode()

    start, _ = await _invoke(mw, body)
    assert upstream.called is True, "when code mode is off, search_tools must be forwarded unchanged"
