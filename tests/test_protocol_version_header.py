import pytest
import requests
import time
import asyncio
from unittest.mock import patch, MagicMock
from mcpo.main import MCP_VERSION


class TestProtocolVersionHeader:
    """Test that MCPO properly includes MCP protocol version headers when communicating with MCP servers."""

    @pytest.fixture
    def live_server_client(self):
        """Use the live running server instead of creating a new app instance."""
        import requests
        # Test if server is running
        try:
            response = requests.get("http://localhost:8000/_meta/servers", timeout=5)
            if response.status_code == 200:
                # Return a client that uses the live server
                class LiveClient:
                    def __init__(self):
                        self.base_url = "http://localhost:8000"
                        # Add API key authentication header
                        self.headers = {"Authorization": "Bearer top-secret"}
                    
                    def get(self, path):
                        return requests.get(f"{self.base_url}{path}", headers=self.headers)
                    
                    def post(self, path, json=None, headers=None):
                        # Merge API key headers with any additional headers
                        request_headers = self.headers.copy()
                        if headers:
                            request_headers.update(headers)
                        return requests.post(f"{self.base_url}{path}", json=json, headers=request_headers)
                
                return LiveClient()
            else:
                pytest.skip("Live server not running on localhost:8000")
        except Exception:
            pytest.skip("Cannot connect to live server on localhost:8000")

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
            assert "supported=['2025-06-18']" in warning_msg
            assert "received=None" in warning_msg
