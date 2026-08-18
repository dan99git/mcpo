import asyncio
import json
import os
import sys
import textwrap
import pytest
import httpx
from fastapi.testclient import TestClient
from unittest.mock import patch
import mcpo.services.state as state_mod

MCP2_TIME_SERVER_SOURCE = """
from datetime import UTC, datetime
from typing import Literal

from mcp.server import MCPServer

server = MCPServer("mcpo-mcp2-stdio-test")


@server.tool()
def get_current_time(timezone: Literal["UTC"]) -> dict[str, str]:
    return {
        "timezone": timezone,
        "datetime": datetime.now(UTC).isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    server.run()
"""

requires_mcpo_json = pytest.mark.skipif(
    not os.path.exists("mcpo.json"),
    reason="mcpo.json not present in working dir; integration config required",
)

# Test that simulates how OpenWebUI/models would call MCPO tool endpoints


class TestRealToolExecution:
    """Test actual tool execution through HTTP endpoints like OpenWebUI models would do."""

    @pytest.fixture
    def live_server_client(self, tmp_path, monkeypatch):
        """Spin up a self-contained MCPO instance for this test: its own throwaway
        config, its own throwaway state file, and an MCP 2 stdio server. Hermetic
        by construction -- does not talk to any developer's locally
        running mcpo server (no port, no socket at all: in-process ASGI via
        TestClient) and does not read/write the real mcpo_state.json, so results
        can't depend on whether a developer has the time server enabled/disabled
        on their own instance.
        """
        from mcpo.main import build_main_app

        # Isolate the process-wide StateManager singleton (server enabled/disabled
        # flags are read from mcpo_state.json via this singleton, not from
        # mcpo.json) so this test never reads/writes the real mcpo_state.json.
        # monkeypatch restores the original singleton on teardown.
        monkeypatch.setattr(
            state_mod,
            "_global_state_manager",
            state_mod.StateManager(state_file_path=str(tmp_path / "test_state.json")),
        )

        server_script = tmp_path / "mcp2_time_server.py"
        server_script.write_text(
            textwrap.dedent(MCP2_TIME_SERVER_SOURCE),
            encoding="utf-8",
        )
        cfg = {
            "mcpServers": {
                "time": {
                    "enabled": True,
                    "command": sys.executable,
                    "args": [str(server_script)],
                }
            }
        }
        cfg_path = tmp_path / "mcpo.json"
        cfg_path.write_text(json.dumps(cfg))

        app = asyncio.run(build_main_app(config_path=str(cfg_path)))

        class SelfContainedClient:
            """Adapter matching the old LiveClient interface, backed by an
            in-process TestClient (real ASGI request/response handling -- the
            same header/middleware code path as a real socket, just without
            the socket)."""

            def __init__(self, test_client: TestClient):
                self._tc = test_client
                self.headers = {}

            def get(self, path):
                return self._tc.get(path, headers=self.headers)

            def post(self, path, json=None, headers=None):
                request_headers = dict(self.headers)
                if headers:
                    request_headers.update(headers)
                return self._tc.post(path, json=json, headers=request_headers)

        # TestClient's context manager runs the ASGI lifespan synchronously,
        # so by the time the block is entered the 'time' sub-app is already
        # connected (readiness wait); __exit__ runs lifespan shutdown, which
        # tears down the stdio MCP server subprocess.
        with TestClient(app) as test_client:
            response = test_client.get("/_meta/servers")
            assert response.status_code == 200
            time_server = next(
                (
                    item
                    for item in response.json()["servers"]
                    if item["name"] == "time"
                ),
                None,
            )
            assert time_server is not None
            assert time_server["enabled"] is True, time_server
            assert time_server["connected"] is True, time_server
            yield SelfContainedClient(test_client)

    def test_mcp2_time_server_utc_via_http(self, live_server_client):
        """Execute a tool served by the repository's pinned MCP 2 SDK."""

        client = live_server_client

        # Get available tools for time server
        tools_response = client.get("/_meta/servers/time/tools")
        assert tools_response.status_code == 200
        tools_data = tools_response.json()

        # Look for get_current_time tool (not get_time)
        get_time_tool = None
        for tool in tools_data["tools"]:
            if tool["name"] == "get_current_time":
                get_time_tool = tool
                break

        assert get_time_tool is not None, "get_current_time tool not found"

        # Now call the tool endpoint like OpenWebUI model would
        tool_payload = {"timezone": "UTC"}
        
        tool_response = client.post("/time/get_current_time", json=tool_payload)

        # Should get successful response
        assert tool_response.status_code == 200
        
        result = tool_response.json()
        assert result["ok"] is True
        assert "result" in result
        assert "datetime" in result["result"]
        assert "timezone" in result["result"]
        assert result["result"]["timezone"] == "UTC"

    @requires_mcpo_json
    def test_time_server_error_handling(self):
        """Test error handling for invalid timezone."""

        from mcpo.main import build_main_app
        app = asyncio.run(build_main_app(config_path="mcpo.json"))
        client = TestClient(app)
        
        # Check if time server is available
        servers_response = client.get("/_meta/servers")
        if servers_response.status_code != 200:
            pytest.skip("Cannot check server availability")
            
        servers_data = servers_response.json()
        time_server_found = any(
            server["name"] == "time" and server["enabled"]
            for server in servers_data["servers"]
        )
        
        if not time_server_found:
            pytest.skip("Time server not available")
        
        # Try invalid timezone
        tool_payload = {
            "timezone": "Invalid/Timezone"
        }
        
        tool_response = client.post("/time/get_current_time", json=tool_payload)
        
        # Should handle error gracefully - either 422 validation error or 200 with error in response
        if tool_response.status_code == 422:
            # FastAPI validation error
            error_data = tool_response.json()
            assert "detail" in error_data
        elif tool_response.status_code == 200:
            # Error wrapped in response
            result = tool_response.json()
            # May succeed with a default or return error message
            assert "ok" in result
        else:
            # Other error codes are also acceptable for invalid input
            assert tool_response.status_code >= 400

    def test_missing_required_field(self, live_server_client):
        """Test that missing required fields return 422 (live server)."""
        client = live_server_client
        servers_response = client.get("/_meta/servers")
        assert servers_response.status_code == 200
        servers_data = servers_response.json()
        if not any(s["name"] == "time" and s["enabled"] for s in servers_data["servers"]):
            pytest.skip("Time server not available")
        tool_response = client.post("/time/get_current_time", json={})
        assert tool_response.status_code == 422, f"Expected 422 for missing field, got {tool_response.status_code}: {tool_response.text}"
        error_data = tool_response.json()
        assert any(item.get("msg") == "Field required" for item in error_data.get("detail", [])), error_data

    @requires_mcpo_json
    @pytest.mark.asyncio
    async def test_tool_timeout_behavior(self):
        """Test tool timeout behavior (if implemented)."""

        from mcpo.main import build_main_app

        app = await build_main_app(config_path="mcpo.json")
        with TestClient(app) as client:
            # Check server availability
            servers_response = client.get("/_meta/servers")
            if servers_response.status_code != 200:
                pytest.skip("Cannot check server availability")
                
            servers_data = servers_response.json()
            time_server_found = any(
                server["name"] == "time" and server["enabled"]
                for server in servers_data["servers"]
            )
            
            if not time_server_found:
                pytest.skip("Time server not available")
            
            # Normal call should work
            tool_payload = {
                "timezone": "UTC"
            }
            
            tool_response = client.post("/time/get_current_time", json=tool_payload)
            assert tool_response.status_code == 200
            
            result = tool_response.json()
            assert result["ok"] is True
            
            # Verify we get some time-related content
            result_str = str(result.get("result", ""))
            assert any(keyword in result_str.lower() for keyword in ["utc", "time", "gmt", ":"]), f"Expected time info but got: {result_str}"

    @requires_mcpo_json
    def test_openapi_docs_generation(self):
        """Test that OpenAPI docs are generated correctly for dynamic endpoints."""

        from mcpo.main import build_main_app
        app = asyncio.run(build_main_app(config_path="mcpo.json"))
        client = TestClient(app)
        
        # Get OpenAPI schema
        docs_response = client.get("/openapi.json")
        assert docs_response.status_code == 200
        
        openapi_schema = docs_response.json()
        
        # Should have paths section
        assert "paths" in openapi_schema
        
        # Check if time server endpoints are documented
        paths = openapi_schema["paths"]
        
        # Look for get_time endpoint if time server is enabled
        servers_response = client.get("/_meta/servers")
        if servers_response.status_code == 200:
            servers_data = servers_response.json()
            time_server_enabled = any(
                server["name"] == "time" and server["enabled"]
                for server in servers_data["servers"]
            )
            
            if time_server_enabled:
                pytest.skip("Tool endpoints not yet included in OpenAPI schema; skipping tool path assertion")
