import os
import json
import tempfile
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import pytest
from fastapi import FastAPI

from mcpo.main import load_config, validate_server_config, reload_config_handler
from mcpo.services.state import StateManager, StateSaveError


def test_validate_server_config_stdio():
    """Test validation of stdio server configuration."""
    config = {"command": "echo", "args": ["hello", "world"]}
    # Should not raise
    validate_server_config("test_server", config)


def test_validate_server_config_sse():
    """Test validation of SSE server configuration."""
    config = {"type": "sse", "url": "http://example.com/sse"}
    # Should not raise
    validate_server_config("test_server", config)


def test_validate_server_config_invalid():
    """Test validation fails for invalid configuration."""
    config = {"invalid": "config"}
    with pytest.raises(
        ValueError, match="must have either 'command' for stdio or 'type' and 'url'"
    ):
        validate_server_config("test_server", config)


def test_validate_server_config_missing_url():
    """Test validation fails for SSE config missing URL."""
    config = {
        "type": "sse"
        # missing url
    }
    with pytest.raises(ValueError, match="requires a 'url' field"):
        validate_server_config("test_server", config)


def test_validate_server_config_disabled_tools_valid():
    """Test validation of server configuration with a valid disabledTools."""
    config = {"command": "echo", "args": ["hello"], "disabledTools": ["search-web"]}
    validate_server_config("test_server", config)


def test_validate_server_config_disabled_tools_invalid():
    """Test validation fails for an invalid disabledTools."""
    config = {"command": "echo", "args": ["hello"], "disabledTools": "not-a-list"}
    with pytest.raises(ValueError, match="'disabledTools' must be a list"):
        validate_server_config("test_server", config)


def test_load_config_valid():
    """Test loading a valid config file."""
    config_data = {
        "mcpServers": {"test_server": {"command": "echo", "args": ["hello"]}}
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        result = load_config(config_path)
        assert result == config_data
    finally:
        os.unlink(config_path)


def test_load_config_invalid_json():
    """Test loading invalid JSON fails."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write('{"invalid": json}')
        config_path = f.name

    try:
        with pytest.raises(json.JSONDecodeError):
            load_config(config_path)
    finally:
        os.unlink(config_path)


def test_load_config_missing_servers():
    """Test loading config without mcpServers fails."""
    config_data = {"other": "data"}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config_data, f)
        config_path = f.name

    try:
        with pytest.raises(ValueError, match="No 'mcpServers' found"):
            load_config(config_path)
    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_reload_config_handler(tmp_path):
    """Test the config reload handler."""
    # Create a mock FastAPI app
    app = FastAPI()
    app.state.state_manager = StateManager(str(tmp_path / "state.json"))
    app.state.config_data = {
        "mcpServers": {"old_server": {"command": "echo", "args": ["old"]}}
    }
    app.state.cors_allow_origins = ["*"]
    app.state.api_key = None
    app.state.strict_auth = False
    app.state.api_dependency = None
    app.state.connection_timeout = None
    app.state.lifespan = None
    app.state.path_prefix = "/"
    app.router.routes = []

    new_config = {"mcpServers": {"new_server": {"command": "echo", "args": ["new"]}}}

    # Mock create_sub_app to avoid actually creating apps. App-local state must
    # prevent reload from touching the process-global runtime state file.
    with (
        patch("mcpo.main.get_state_manager", side_effect=AssertionError("global state used")),
        patch("mcpo.main.create_sub_app") as mock_create_sub_app,
    ):
        mock_sub_app = Mock()
        mock_create_sub_app.return_value = mock_sub_app

        # Mock app.mount to avoid actual mounting
        app.mount = Mock()

        await reload_config_handler(app, new_config)

        # Verify the config was updated
        assert app.state.config_data == new_config

        # Verify create_sub_app was called for the new server
        mock_create_sub_app.assert_called_once()

        # Verify mount was called
        app.mount.assert_called_once()


@pytest.mark.asyncio
async def test_reload_aborts_when_state_persistence_fails():
    app = FastAPI()
    old_config = {"mcpServers": {}}
    app.state.config_data = old_config
    app.state.cors_allow_origins = ["*"]
    app.state.api_key = None
    app.state.strict_auth = False
    app.state.api_dependency = None
    app.state.connection_timeout = None
    app.state.lifespan = None
    app.state.path_prefix = "/"
    app.router.routes = []

    new_config = {
        "mcpServers": {"new_server": {"command": "echo", "args": ["new"]}}
    }
    state_manager = Mock()
    state_manager.is_server_enabled.return_value = True
    state_manager.get_server_state.return_value = {"enabled": True, "tools": {}}
    state_manager.set_server_enabled.side_effect = StateSaveError("disk full")

    with (
        patch("mcpo.main.get_state_manager", return_value=state_manager),
        patch("mcpo.main.create_sub_app", return_value=FastAPI()),
    ):
        with pytest.raises(StateSaveError, match="disk full"):
            await reload_config_handler(app, new_config)

    assert app.state.config_data == old_config
    assert app.router.routes == []

def test_config_watcher_initialization():
    """Test ConfigWatcher can be initialized."""
    from mcpo.utils.config_watcher import ConfigWatcher

    callback = Mock()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"mcpServers": {}}, f)
        config_path = f.name

    try:
        watcher = ConfigWatcher(config_path, callback)
        assert watcher.config_path.name == os.path.basename(config_path)
        assert watcher.reload_callback == callback
    finally:
        os.unlink(config_path)


@pytest.mark.asyncio
async def test_config_watcher_normalizes_nested_config_before_callback(tmp_path, monkeypatch):
    """Watched reloads support the nested config.mcpServers shape."""
    from mcpo.utils.config_watcher import ConfigChangeHandler

    monkeypatch.setenv("HOT_RELOAD_TOKEN", "resolved")
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(json.dumps({
        "config": {
            "mcpServers": {
                "nested_server": {
                    "command": "echo",
                    "env": {"TOKEN": "${HOT_RELOAD_TOKEN}"},
                }
            }
        },
        "server": "mcpo",
    }))

    received = {}

    async def reload_callback(new_config):
        received["config"] = new_config

    handler = ConfigChangeHandler(
        config_path,
        reload_callback,
        asyncio.get_running_loop(),
    )

    await handler._handle_config_change()

    assert received["config"]["mcpServers"]["nested_server"]["command"] == "echo"
    assert received["config"]["mcpServers"]["nested_server"]["env"]["TOKEN"] == "resolved"


@pytest.mark.asyncio
async def test_reload_aborts_before_route_replacement_when_teardown_fails(
    tmp_path,
):
    from mcpo.main import _find_server_mounts, build_main_app
    from mcpo.services.state import StateManager

    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "s1": {"command": "echo", "args": ["old"]}
                }
            }
        )
    )
    state = StateManager(str(tmp_path / "state.json"))
    with patch("mcpo.main.get_state_manager", return_value=state):
        app = await build_main_app(config_path=str(config_path))

    old_route = _find_server_mounts(app, "s1")[0]
    runtime = SimpleNamespace(
        connected=True,
        last_error=None,
        task=SimpleNamespace(done=lambda: False),
        bound_app=old_route.app,
    )
    app.state.server_runtimes = {"s1": runtime}
    teardown = AsyncMock(
        return_value={"ok": False, "error": "still running"}
    )
    candidate = {
        "mcpServers": {"s1": {"command": "echo", "args": ["new"]}}
    }

    with (
        patch("mcpo.main.get_state_manager", return_value=state),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        pytest.raises(RuntimeError, match="still running"),
    ):
        await reload_config_handler(app, candidate)

    assert _find_server_mounts(app, "s1") == [old_route]
    assert app.state.server_runtimes["s1"] is runtime
    assert runtime.bound_app is old_route.app


@pytest.mark.asyncio
async def test_hot_reload_callback_rereads_file_and_syncs_native_proxy(
    tmp_path,
):
    from mcpo.main import build_main_app
    from mcpo.services.state import StateManager

    captured = {}

    class CapturingWatcher:
        def __init__(self, config_path, callback):
            self.config_path = config_path
            self.callback = callback
            self.started = False
            captured["watcher"] = self

        def start(self):
            self.started = True

        def stop(self):
            pass

    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "marker": "old",
                "mcpServers": {
                    "s1": {"command": "echo", "args": ["old"]}
                },
            }
        )
    )
    state = StateManager(str(tmp_path / "state.json"))

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    remount = AsyncMock()
    with (
        patch("mcpo.main.get_state_manager", return_value=state),
        patch("mcpo.main.ConfigWatcher", CapturingWatcher),
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        app = await build_main_app(
            config_path=str(config_path),
            hot_reload=True,
        )
        app.state.fastmcp_proxy_mounts = []
        candidate = {
            "marker": "fresh",
            "mcpServers": {
                "s1": {"command": "echo", "args": ["fresh"]}
            },
        }
        config_path.write_text(json.dumps(candidate))
        await captured["watcher"].callback(
            {"marker": "stale", "mcpServers": {}}
        )

    assert captured["watcher"].started is True
    assert app.state.config_watcher is captured["watcher"]
    assert app.state.config_data["marker"] == "fresh"
    remount.assert_awaited_once_with(app, base_path="/mcp")
