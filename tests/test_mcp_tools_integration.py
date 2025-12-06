import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from starlette.routing import Mount
from mcpo.services.mcp_tools import collect_enabled_mcp_sessions, collect_enabled_mcp_sessions_with_names


@pytest.fixture
def mock_app():
    app = FastAPI()
    # Mock some mounted sub-apps
    mock_sub_app1 = MagicMock()
    mock_sub_app1.state.is_fastmcp_proxy = False
    mock_sub_app1.state.is_connected = True
    mock_sub_app1.state.session = MagicMock()

    mock_sub_app2 = MagicMock()
    mock_sub_app2.state.is_fastmcp_proxy = False
    mock_sub_app2.state.is_connected = False  # Not connected
    mock_sub_app2.state.session = MagicMock()

    mock_sub_app3 = MagicMock()
    mock_sub_app3.state.is_fastmcp_proxy = True  # FastMCP proxy, should be skipped
    mock_sub_app3.state.session = MagicMock()

    app.router.routes = [
        Mount(path="/server1", app=mock_sub_app1),
        Mount(path="/server2", app=mock_sub_app2),
        Mount(path="/server3", app=mock_sub_app3),
    ]
    return app


def test_collect_enabled_mcp_sessions(mock_app):
    with patch('mcpo.services.mcp_tools.get_state_manager') as mock_state_mgr:
        mock_state = MagicMock()
        mock_state.is_server_enabled.side_effect = lambda name: name != "server2"  # server2 disabled
        mock_state_mgr.return_value = mock_state

        sessions = collect_enabled_mcp_sessions(mock_app, connected_only=True)

        # Should only return session from server1 (enabled and connected)
        assert len(sessions) == 1


def test_collect_enabled_mcp_sessions_with_allowlist(mock_app):
    with patch('mcpo.services.mcp_tools.get_state_manager') as mock_state_mgr:
        mock_state = MagicMock()
        mock_state.is_server_enabled.return_value = True
        mock_state_mgr.return_value = mock_state

        sessions = collect_enabled_mcp_sessions(mock_app, allowlist=["server1"], connected_only=False)

        # Should only return session from server1
        assert len(sessions) == 1


def test_collect_enabled_mcp_sessions_with_names(mock_app):
    with patch('mcpo.services.mcp_tools.get_state_manager') as mock_state_mgr:
        mock_state = MagicMock()
        mock_state.is_server_enabled.return_value = True
        mock_state_mgr.return_value = mock_state

        sessions_with_names = collect_enabled_mcp_sessions_with_names(mock_app, connected_only=False)

        # Should return tuples for server1 and server2 (server3 is proxy)
        assert len(sessions_with_names) == 2
        names, sessions = zip(*sessions_with_names)
        assert "server1" in names
        assert "server2" in names


def test_collect_enabled_mcp_sessions_skip_proxy(mock_app):
    with patch('mcpo.services.mcp_tools.get_state_manager') as mock_state_mgr:
        mock_state = MagicMock()
        mock_state.is_server_enabled.return_value = True
        mock_state_mgr.return_value = mock_state

        sessions = collect_enabled_mcp_sessions(mock_app, connected_only=False)

        # Should skip server3 (FastMCP proxy)
        assert len(sessions) == 2
