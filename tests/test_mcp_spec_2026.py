"""Wire-level regression tests for the MCP 2026-07-28 proxy boundary."""

from __future__ import annotations

import json
from typing import Annotated, Any

import pytest
from fastmcp import FastMCP
from pydantic import Field
from starlette.testclient import TestClient

from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.middleware.mcp_tool_filter import MCPToolFilterMiddleware
from mcpo.proxy import _mcp_transport_security_kwargs

PROTOCOL_VERSION = "2026-07-28"
PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"


class FakeStateManager:
    def __init__(self) -> None:
        self.code_mode = False
        self.states = {
            "alpha": {
                "enabled": True,
                "tools": {
                    "danger": False,
                    "safe": True,
                    "zeta": True,
                },
            }
        }

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


def modern_request(
    method: str,
    *,
    request_id: int = 1,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        PROTOCOL_VERSION_KEY: PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_KEY: {},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": request_params,
    }


def modern_headers(method: str, *, name: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


@pytest.fixture
def mcp_client():
    server = FastMCP(
        "mcpo-wire-test",
        instructions="native discovery instructions",
    )

    @server.tool(name="zeta")
    def zeta() -> str:
        return "zeta"

    @server.tool(name="danger")
    def danger() -> str:
        return "danger"

    @server.tool(name="safe")
    def safe(
        value: Annotated[
            str,
            Field(json_schema_extra={"x-mcp-header": "value"}),
        ] = "ok",
    ) -> str:
        return value

    state = FakeStateManager()
    code_mode = CodeModeMCPMiddleware(server_name="alpha")
    tool_filter = MCPToolFilterMiddleware(server_name="alpha")
    code_mode.state_manager = state
    tool_filter.state_manager = state
    server.add_middleware(code_mode)
    server.add_middleware(tool_filter)

    app = server.http_app(
        path="/mcp",
        transport="streamable-http",
        json_response=True,
        **_mcp_transport_security_kwargs(),
    )
    with TestClient(app) as client:
        yield client, state


def test_native_discovery_preserves_real_capabilities_and_safe_cache(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=modern_request("server/discover"),
        headers=modern_headers("server/discover"),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["supportedVersions"] == [PROTOCOL_VERSION]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["instructions"] == "native discovery instructions"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["capabilities"]["resources"]["subscribe"] is False
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "mcpo-wire-test"


def test_missing_modern_meta_is_rejected_before_disabled_tool_policy(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "danger", "arguments": {}},
        },
        headers=modern_headers("tools/call", name="danger"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


def test_required_method_and_name_headers_are_enforced(mcp_client) -> None:
    client, _state = mcp_client
    request = modern_request(
        "tools/call",
        params={"name": "safe", "arguments": {}},
    )

    missing_name = client.post(
        "/mcp",
        json=request,
        headers=modern_headers("tools/call"),
    )
    wrong_method = client.post(
        "/mcp",
        json=request,
        headers=modern_headers("tools/list", name="safe"),
    )

    assert missing_name.status_code == 400
    assert missing_name.json()["error"]["code"] == -32020
    assert wrong_method.status_code == 400
    assert wrong_method.json()["error"]["code"] == -32020


def test_mcp_param_headers_are_checked_against_tool_schema(mcp_client) -> None:
    client, _state = mcp_client
    headers = modern_headers("tools/call", name="safe")
    headers["Mcp-Param-value"] = "header-value"
    response = client.post(
        "/mcp",
        json=modern_request(
            "tools/call",
            params={"name": "safe", "arguments": {"value": "body-value"}},
        ),
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020

def test_modern_batch_is_rejected_by_sdk(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=[modern_request("tools/list")],
        headers=modern_headers("tools/list"),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


def test_tools_list_is_filtered_sorted_and_immediately_stale(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=modern_request("tools/list"),
        headers=modern_headers("tools/list"),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert [tool["name"] for tool in result["tools"]] == ["safe", "zeta"]
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["resultType"] == "complete"


def test_disabled_tool_is_blocked_after_validation(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=modern_request(
            "tools/call",
            params={"name": "danger", "arguments": {}},
        ),
        headers=modern_headers("tools/call", name="danger"),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["_meta"]["mcpo"] == {
        "code": "tool_disabled",
        "tool": "danger",
        "server": "alpha",
    }


def test_invalid_origin_is_rejected_before_mcp_dispatch(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=modern_request("server/discover"),
        headers={
            **modern_headers("server/discover"),
            "Origin": "https://attacker.invalid",
        },
    )

    assert response.status_code == 403
    assert response.text == "Forbidden Origin"


def test_documented_docker_host_reaches_mcp_dispatch(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        json=modern_request("server/discover"),
        headers={
            **modern_headers("server/discover"),
            "Host": "host.docker.internal:8001",
        },
    )

    assert response.status_code == 200


def test_outer_boundary_keeps_sdk_four_mib_body_limit(mcp_client) -> None:
    client, _state = mcp_client
    response = client.post(
        "/mcp",
        content=b"x" * ((4 * 1024 * 1024) + 1),
        headers={
            **modern_headers("server/discover"),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413


def test_code_mode_lists_and_executes_meta_tools_after_validation(mcp_client) -> None:
    client, state = mcp_client
    state.code_mode = True

    listed = client.post(
        "/mcp",
        json=modern_request("tools/list"),
        headers=modern_headers("tools/list"),
    )
    assert listed.status_code == 200
    assert [tool["name"] for tool in listed.json()["result"]["tools"]] == [
        "search_tools",
        "execute_tool",
    ]

    searched = client.post(
        "/mcp",
        json=modern_request(
            "tools/call",
            request_id=8,
            params={
                "name": "search_tools",
                "arguments": {"query": "safe", "limit": 10},
            },
        ),
        headers=modern_headers("tools/call", name="search_tools"),
    )
    assert searched.status_code == 200
    search_payload = json.loads(
        searched.json()["result"]["content"][0]["text"]
    )
    assert [item["tool"] for item in search_payload["tools"]] == ["alpha.safe"]

    executed = client.post(
        "/mcp",
        json=modern_request(
            "tools/call",
            request_id=9,
            params={
                "name": "execute_tool",
                "arguments": {
                    "tool": "alpha.safe",
                    "arguments": {"value": "through-code-mode"},
                },
            },
        ),
        headers=modern_headers("tools/call", name="execute_tool"),
    )
    assert executed.status_code == 200
    result = executed.json()["result"]
    assert result["isError"] is False
    assert result["resultType"] == "complete"
    assert result["content"][0]["text"] == "through-code-mode"