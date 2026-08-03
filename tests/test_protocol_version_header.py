import json
import shutil
import pytest
import time
import asyncio
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from mcpo.main import MCP_VERSION, build_main_app
from mcpo.utils.main import SUPPORTED_MCP_VERSIONS
import mcpo.services.state as state_mod


class TestProtocolVersionHeader:
    """Test that MCPO properly includes MCP protocol version headers when communicating with MCP servers."""

    @pytest.fixture
    def live_server_client(self, tmp_path, monkeypatch):
        """Spin up a self-contained MCPO instance for this test: its own throwaway
        config, its own throwaway state file, and a real 'time' MCP server (via
        uvx). Hermetic by construction -- does not talk to any developer's locally
        running mcpo server (no port, no socket at all: in-process ASGI via
        TestClient) and does not read/write the real mcpo_state.json, so results
        can't depend on whether a developer has the time server enabled/disabled
        on their own instance.
        """
        if shutil.which("uvx") is None:
            pytest.skip("uvx not available; cannot start a real MCP time server for this test")

        # Isolate the process-wide StateManager singleton (server enabled/disabled
        # flags are read from mcpo_state.json via this singleton, not from
        # mcpo.json) so this test never reads/writes the real mcpo_state.json.
        # monkeypatch restores the original singleton on teardown.
        monkeypatch.setattr(
            state_mod,
            "_global_state_manager",
            state_mod.StateManager(state_file_path=str(tmp_path / "test_state.json")),
        )

        cfg = {
            "mcpServers": {
                "time": {
                    "enabled": True,
                    "command": "uvx",
                    # This vendor release imports the pre-2.0 McpError name.
                    # Pin both the package and its compatible SDK range.
                    "args": [
                        "--from",
                        "mcp-server-time==2026.7.10",
                        "--with",
                        "mcp==1.29.0",
                        "mcp-server-time",
                    ],
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

    def test_http_client_without_protocol_header_still_works(self, live_server_client):
        """Test that HTTP clients without MCP-Protocol-Version header still work."""
        
        client = live_server_client
        
        # First verify time server is available
        servers_response = client.get("/_meta/servers")
        assert servers_response.status_code == 200
        servers_data = servers_response.json()

        time_server_found = False
        for server in servers_data["servers"]:
            if server["name"] == "time":
                time_server_found = True
                assert server["enabled"] is True
                break

        if not time_server_found:
            pytest.skip("Time server not configured or not enabled")

        # Call tool WITHOUT MCP-Protocol-Version header (like OpenWebUI would)
        tool_payload = {
            "timezone": "UTC"
        }
        
        tool_response = client.post("/time/get_current_time", json=tool_payload)
        
        # Should still work despite missing MCP protocol header
        assert tool_response.status_code == 200
        result = tool_response.json()
        assert result["ok"] is True
        assert "result" in result

    def test_http_client_with_protocol_header_works(self, live_server_client):
        """Test that HTTP clients WITH MCP-Protocol-Version header work properly."""
        
        client = live_server_client
        
        # First verify time server is available
        servers_response = client.get("/_meta/servers")
        assert servers_response.status_code == 200
        servers_data = servers_response.json()

        time_server_found = False
        for server in servers_data["servers"]:
            if server["name"] == "time":
                time_server_found = True
                break

        if not time_server_found:
            pytest.skip("Time server not configured or not enabled")

        # Call tool WITH MCP-Protocol-Version header
        tool_payload = {
            "timezone": "UTC"
        }
        
        headers = {
            "MCP-Protocol-Version": MCP_VERSION
        }
        
        tool_response = client.post("/time/get_current_time", json=tool_payload, headers=headers)
        
        # Should work with proper protocol header
        assert tool_response.status_code == 200
        result = tool_response.json()
        assert result["ok"] is True
        assert "result" in result

    def test_protocol_version_constant_matches_expected(self):
        """Test that the MCP_VERSION constant matches expected value."""
        
        # Verify the constant is set to the expected version
        assert MCP_VERSION == "2025-06-18"

    def test_protocol_version_warning_capture(self, live_server_client):
        """Test to capture protocol version warnings in logs."""
        
        client = live_server_client
        
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
        
        # Get logs before making call
        logs_before = client.get("/_meta/logs")
        before_count = len(logs_before.json()["logs"]) if logs_before.status_code == 200 else 0
        
        # Make a tool call without protocol version header
        tool_payload = {
            "timezone": "UTC"
        }
        
        tool_response = client.post("/time/get_current_time", json=tool_payload)
        assert tool_response.status_code == 200
        
        # Small delay to ensure log entry is written
        time.sleep(0.5)
        
        # Get logs after making call
        logs_after = client.get("/_meta/logs")
        if logs_after.status_code == 200:
            logs_data = logs_after.json()["logs"]
            
            # Look for protocol version warning in recent logs
            protocol_warnings = [
                log for log in logs_data 
                if "Protocol warn" in log.get("message", "") 
                and "MCP-Protocol-Version" in log.get("message", "")
            ]
            
            # We expect to see protocol version warnings
            assert len(protocol_warnings) > 0, "Expected to see MCP protocol version warnings in logs"
            
            # Verify the warning message content
            warning_msg = protocol_warnings[-1]["message"]  # Get most recent
            assert "Unsupported or missing MCP-Protocol-Version" in warning_msg
            assert f"supported={SUPPORTED_MCP_VERSIONS}" in warning_msg
            assert "received=None" in warning_msg
