"""Comprehensive tests for all /_meta/* management endpoints.

Each test is self-contained: builds its own app, mounts fake sub-apps where needed,
and tears down cleanly via tmp_path for config files and patching the global
StateManager singleton so tests don't bleed into each other.
"""
import asyncio
import json
import os
import httpx
import pytest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path, servers=None):
    """Write a minimal mcpo.json and return its path as str."""
    if servers is None:
        servers = {"s1": {"command": "echo", "args": ["ok"]}}
    cfg_path = tmp_path / "mcpo.json"
    cfg_path.write_text(json.dumps({"mcpServers": servers}))
    return str(cfg_path)


def _fresh_state_manager(tmp_path):
    """Return a factory that creates a StateManager with an isolated state file."""
    from mcpo.services.state import StateManager
    path = str(tmp_path / "test_state.json")
    return StateManager(state_file_path=path)


async def _build_app(tmp_path, *, servers=None, read_only=False, extra_kwargs=None):
    """Build a main app with patched state manager pointing at tmp_path."""
    from mcpo.main import build_main_app
    cfg_path = _write_cfg(tmp_path, servers)
    sm = _fresh_state_manager(tmp_path)
    kwargs = dict(config_path=cfg_path, read_only=read_only)
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app(**kwargs)
    # Ensure the app state has the patched state manager
    app.state.state_manager = sm
    return app


async def _build_wrapped_app(tmp_path, *, servers=None):
    """Build from the nested wrapper shape used by the checked-in mcpo.json."""
    from mcpo.main import build_main_app

    if servers is None:
        servers = {"s1": {"command": "echo", "args": ["ok"]}}
    cfg_path = tmp_path / "mcpo.json"
    cfg_path.write_text(
        json.dumps(
            {"server": "mcpo", "config": {"keep": "unchanged", "mcpServers": servers}}
        )
    )
    sm = _fresh_state_manager(tmp_path)
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app(config_path=str(cfg_path))
    app.state.state_manager = sm
    return app


def _mount_fake_server(app, name="s1", tools=None):
    """Mount a fake FastAPI sub-app simulating a connected MCP server.

    ``tools`` is a list of tool name strings to register as POST endpoints.
    Returns the sub-app.
    """
    from mcpo.utils.main import get_tool_handler

    sub = FastAPI(title=name)
    sub.state.parent_app = app
    sub.state.is_connected = True
    sub.state.server_type = "stdio"
    sub.state.config_key = name

    class FakeSession:
        async def call_tool(self, name, arguments):
            return type("R", (), {"is_error": False, "content": []})()

    sub.state.session = FakeSession()

    for tname in (tools or []):
        handler = get_tool_handler(sub.state.session, tname, form_model_fields=None)
        sub.post(f"/{tname}")(handler)

    app.mount(f"/{name}", sub)
    return sub


# ===========================================================================
# 1. GET /_meta/servers — list servers, verify structure
# ===========================================================================

@pytest.mark.asyncio
async def test_list_servers_returns_ok_and_structure(tmp_path):
    app = await _build_app(tmp_path)
    _mount_fake_server(app, "s1", tools=["ping"])
    client = TestClient(app)

    r = client.get("/_meta/servers")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["servers"], list)
    names = {s["name"] for s in body["servers"]}
    assert "s1" in names
    s1 = next(s for s in body["servers"] if s["name"] == "s1")
    assert "connected" in s1
    assert "type" in s1
    assert "basePath" in s1
    assert "enabled" in s1


@pytest.mark.asyncio
async def test_list_servers_empty_config(tmp_path):
    app = await _build_app(tmp_path, servers={})
    client = TestClient(app)

    r = client.get("/_meta/servers")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # May contain internal mcpo server; should not error
    assert isinstance(body["servers"], list)


# ===========================================================================
# 2. GET /_meta/servers/{name}/tools — list tools for a server
# ===========================================================================

@pytest.mark.asyncio
async def test_list_server_tools_happy(tmp_path):
    app = await _build_app(tmp_path, servers={})
    _mount_fake_server(app, "s1", tools=["alpha", "beta"])
    client = TestClient(app)

    r = client.get("/_meta/servers/s1/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "s1"
    tool_names = [t["name"] for t in body["tools"]]
    assert "alpha" in tool_names
    assert "beta" in tool_names
    for t in body["tools"]:
        assert "enabled" in t


@pytest.mark.asyncio
async def test_list_server_tools_not_found(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/_meta/servers/nonexistent/tools")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_non_root_path_prefix_discovery_tools_and_reinit(tmp_path):
    from starlette.routing import Mount

    app = await _build_app(
        tmp_path,
        extra_kwargs={"path_prefix": "/api/"},
    )
    route = next(
        route
        for route in app.router.routes
        if isinstance(route, Mount) and route.path == "/api/s1"
    )
    sub = route.app
    sub.state.is_connected = True

    @sub.post("/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app)

    servers_response = client.get("/_meta/servers")
    assert servers_response.status_code == 200
    servers = {item["name"]: item for item in servers_response.json()["servers"]}
    assert servers["s1"]["connected"] is True
    assert servers["s1"]["basePath"] == "/api/s1/"

    tools_response = client.get("/_meta/servers/s1/tools")
    assert tools_response.status_code == 200
    assert [tool["name"] for tool in tools_response.json()["tools"]] == ["ping"]

    async def fake_init(sub_app):
        sub_app.state.is_connected = True

    with (
        patch("mcpo.main.initialize_sub_app", side_effect=fake_init),
        patch(
            "mcpo.main.teardown_server_runtime",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        reinit_response = client.post("/_meta/reinit/s1")

    assert reinit_response.status_code == 200
    assert reinit_response.json() == {
        "ok": True,
        "server": "s1",
        "connected": True,
    }


@pytest.mark.asyncio
async def test_lifespan_uses_config_key_with_non_root_path_prefix(tmp_path):
    from mcpo.main import lifespan

    app = await _build_app(
        tmp_path,
        extra_kwargs={"path_prefix": "/api/"},
    )
    runtime = SimpleNamespace(connected=True, last_error=None)
    spawn = AsyncMock(return_value=runtime)

    with (
        patch("mcpo.main.get_state_manager", return_value=app.state.state_manager),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
    ):
        async with lifespan(app):
            pass

    spawn.assert_awaited_once()
    assert spawn.await_args.args[1] == "s1"

# ===========================================================================
# 3. POST /_meta/servers/{name}/enable — enable server
# ===========================================================================

@pytest.mark.asyncio
async def test_enable_server_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    # Disable first
    r = client.post("/_meta/servers/s1/disable")
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    # Enable
    with patch("mcpo.main.spawn_server_runtime", new=AsyncMock(return_value=SimpleNamespace(connected=True, last_error=None))):
        r = client.post("/_meta/servers/s1/enable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "s1"
    assert body["enabled"] is True


@pytest.mark.asyncio
async def test_enable_server_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/enable")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


# ===========================================================================
# 4. POST /_meta/servers/{name}/disable — disable server
# ===========================================================================

@pytest.mark.asyncio
async def test_disable_server_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/disable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["enabled"] is False

    # Verify via list
    r2 = client.get("/_meta/servers")
    servers = {s["name"]: s for s in r2.json()["servers"]}
    if "s1" in servers:
        assert servers["s1"]["enabled"] is False


@pytest.mark.asyncio
async def test_disable_server_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/disable")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


# ===========================================================================
# 5. POST /_meta/servers/{name}/tools/{tool}/enable — enable tool
# ===========================================================================

@pytest.mark.asyncio
async def test_enable_tool_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    # Disable first
    r = client.post("/_meta/servers/s1/tools/ping/disable")
    assert r.status_code == 200

    # Enable
    r = client.post("/_meta/servers/s1/tools/ping/enable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "s1"
    assert body["tool"] == "ping"
    assert body["enabled"] is True


@pytest.mark.asyncio
async def test_enable_tool_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/tools/ping/enable")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


# ===========================================================================
# 6. POST /_meta/servers/{name}/tools/{tool}/disable — disable tool
# ===========================================================================

@pytest.mark.asyncio
async def test_disable_tool_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/tools/ping/disable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "s1"
    assert body["tool"] == "ping"
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_disable_tool_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/servers/s1/tools/ping/disable")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


# ===========================================================================
# 7. GET /_meta/config — get config info
# ===========================================================================

@pytest.mark.asyncio
async def test_config_info_returns_path(tmp_path):
    cfg_path = _write_cfg(tmp_path)
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app(config_path=cfg_path)
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.get("/_meta/config")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["configPath"] == cfg_path


@pytest.mark.asyncio
async def test_config_info_no_config(tmp_path):
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app()
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.get("/_meta/config")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # configPath may be None when no config file supplied
    assert "configPath" in body


# ===========================================================================
# 8. GET /_meta/metrics — get metrics
# ===========================================================================

@pytest.mark.asyncio
async def test_metrics_returns_structure(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/_meta/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    m = body["metrics"]
    assert "servers" in m
    assert "total" in m["servers"]
    assert "enabled" in m["servers"]
    assert "tools" in m
    assert "calls" in m
    assert "errors" in m
    assert "perTool" in m


@pytest.mark.asyncio
async def test_metrics_reflects_disabled_server(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    # Disable s1 to move the counter
    client.post("/_meta/servers/s1/disable")

    r = client.get("/_meta/metrics")
    assert r.status_code == 200
    m = r.json()["metrics"]
    # At least the entry should exist; enabled count may decrease
    assert isinstance(m["servers"]["total"], int)
    assert isinstance(m["servers"]["enabled"], int)


# ===========================================================================
# 9. POST /_meta/reload — reload config (with read-only check)
# ===========================================================================

@pytest.mark.asyncio
async def test_reload_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/reload")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "generation" in body


@pytest.mark.asyncio
async def test_reload_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/reload")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


@pytest.mark.asyncio
async def test_reload_no_config(tmp_path):
    """Reload when no config path is set should return 400."""
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app()
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.post("/_meta/reload")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "no_config"


# ===========================================================================
# 10. POST /_meta/reinit/{name} — reinit server (404 for unknown)
# ===========================================================================

@pytest.mark.asyncio
async def test_reinit_unknown_server(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/reinit/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_reinit_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/reinit/s1")
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


@pytest.mark.asyncio
async def test_reinit_mounted_server(tmp_path):
    """Reinit on a mounted fake server should succeed (calls initialize_sub_app)."""
    app = await _build_app(tmp_path)
    sub = _mount_fake_server(app, "s1", tools=["ping"])

    # Patch initialize_sub_app to avoid real MCP connection
    async def fake_init(sub_app):
        sub_app.state.is_connected = True

    with patch("mcpo.main.initialize_sub_app", side_effect=fake_init):
        client = TestClient(app)
        r = client.post("/_meta/reinit/s1")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "s1"
    assert "connected" in body




@pytest.mark.asyncio
async def test_reinit_teardown_failure_is_fatal(tmp_path):
    app = await _build_app(tmp_path)
    _mount_fake_server(app, "s1", tools=["ping"])
    teardown = AsyncMock(
        return_value={
            "ok": False,
            "server": "s1",
            "error": "still running",
        }
    )
    initialize = AsyncMock()

    with (
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main.initialize_sub_app", new=initialize),
    ):
        response = TestClient(app).post("/_meta/reinit/s1")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "teardown_failed"
    initialize.assert_not_awaited()

# ===========================================================================
# 11. POST /_meta/servers — add server (with validation)
# ===========================================================================

@pytest.mark.asyncio
async def test_add_server_happy(tmp_path):
    app = await _build_app(tmp_path, servers={})
    client = TestClient(app)

    # Patch reload to avoid real MCP connection attempts
    observed = {}

    async def fake_reload(app, cfg):
        observed["old_servers"] = set(app.state.config_data["mcpServers"])
        observed["new_servers"] = set(cfg["mcpServers"])
        app.state.config_data = cfg

    remount = AsyncMock()
    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        r = client.post("/_meta/servers", json={"name": "new1", "command": "echo hello"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "new1"

    # Verify config file was updated
    cfg = json.loads((tmp_path / "mcpo.json").read_text())
    assert "new1" in cfg["mcpServers"]
    assert observed == {
        "old_servers": set(),
        "new_servers": {"new1"},
    }
    remount.assert_awaited_once_with(app, base_path="/mcp")


@pytest.mark.asyncio
async def test_add_server_remount_failure_rolls_back_file_and_state(tmp_path):
    app = await _build_app(tmp_path, servers={})
    config_path = tmp_path / "mcpo.json"
    original_bytes = config_path.read_bytes()
    original_config = json.loads(json.dumps(app.state.config_data))
    client = TestClient(app)

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    reload_mock = AsyncMock(side_effect=fake_reload)
    remount = AsyncMock(side_effect=[RuntimeError("remount failed"), None])
    with (
        patch("mcpo.main.reload_config_handler", new=reload_mock),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = client.post(
            "/_meta/servers",
            json={"name": "new1", "command": "echo"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "reload_failed"
    assert config_path.read_bytes() == original_bytes
    assert app.state.config_data == original_config
    assert reload_mock.await_count == 2
    assert remount.await_count == 2


@pytest.mark.asyncio
async def test_add_server_atomic_write_failure_preserves_file(tmp_path):
    app = await _build_app(tmp_path, servers={})
    config_path = tmp_path / "mcpo.json"
    original_bytes = config_path.read_bytes()
    client = TestClient(app)
    reload_mock = AsyncMock()
    remount = AsyncMock()

    with (
        patch("mcpo.main.os.replace", side_effect=OSError("replace denied")),
        patch("mcpo.main.reload_config_handler", new=reload_mock),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = client.post(
            "/_meta/servers",
            json={"name": "new1", "command": "echo"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "io_error"
    assert config_path.read_bytes() == original_bytes
    assert list(tmp_path.glob(".mcpo.json.*.tmp")) == []
    reload_mock.assert_not_awaited()
    remount.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_server_missing_name(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"command": "echo"})
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid"


@pytest.mark.asyncio
async def test_add_server_duplicate(tmp_path):
    app = await _build_app(tmp_path, servers={"s1": {"command": "echo", "args": ["ok"]}})
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"name": "s1", "command": "echo"})
    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "exists"


@pytest.mark.asyncio
async def test_add_server_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"name": "new1", "command": "echo"})
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "read_only"


@pytest.mark.asyncio
async def test_add_server_invalid_json(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/servers", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_add_server_empty_command(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"name": "bad", "command": "   "})
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False


@pytest.mark.asyncio
async def test_add_server_no_config_mode(tmp_path):
    """Cannot add servers when not running in config-file mode."""
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app()
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"name": "x", "command": "echo"})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "no_config_mode"


# ===========================================================================
# 12. GET /healthz — health check
# ===========================================================================

@pytest.mark.asyncio
async def test_healthz_returns_ok(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "generation" in body
    assert "lastReload" in body
    assert "servers" in body


@pytest.mark.asyncio
async def test_healthz_reflects_mounted_servers(tmp_path):
    app = await _build_app(tmp_path)
    _mount_fake_server(app, "s1", tools=["ping"])
    client = TestClient(app)

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    # The servers dict should mention s1 (by title)
    assert isinstance(body["servers"], dict)


# ===========================================================================
# 13. POST /_meta/env — env update (admin router)
# ===========================================================================

@pytest.mark.asyncio
async def test_env_update_happy(tmp_path, monkeypatch):
    app = await _build_app(tmp_path)
    # Point .env path to tmp_path so we don't clobber real .env
    env_file = tmp_path / ".env"
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/env", json={"TEST_KEY_XYZ": "val123"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "updated"
    assert body["count"] == 1

    # Verify the file was written
    content = env_file.read_text()
    assert "TEST_KEY_XYZ=val123" in content

    # Verify process env updated
    assert os.environ.get("TEST_KEY_XYZ") == "val123"

    # Cleanup
    if "TEST_KEY_XYZ" in os.environ:
        del os.environ["TEST_KEY_XYZ"]


@pytest.mark.asyncio
async def test_env_update_merges_existing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING_KEY=old\n")

    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/env", json={"NEW_KEY": "new_val"})
    assert r.status_code == 200

    content = env_file.read_text()
    assert "EXISTING_KEY=old" in content
    assert "NEW_KEY=new_val" in content

    # Cleanup
    for k in ("EXISTING_KEY", "NEW_KEY"):
        os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_env_update_empty_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.post("/_meta/env", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0


# ===========================================================================
# 14. DELETE /_meta/servers/{name} — remove server
# ===========================================================================

@pytest.mark.asyncio
async def test_remove_server_happy(tmp_path):
    app = await _build_app(tmp_path, servers={"s1": {"command": "echo", "args": ["ok"]}})
    client = TestClient(app)

    observed = {}

    async def fake_reload(app, cfg):
        observed["old_servers"] = set(app.state.config_data["mcpServers"])
        observed["new_servers"] = set(cfg["mcpServers"])
        app.state.config_data = cfg

    remount = AsyncMock()
    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        r = client.delete("/_meta/servers/s1")

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["removed"] == "s1"

    # Verify config file updated
    cfg = json.loads((tmp_path / "mcpo.json").read_text())
    assert "s1" not in cfg.get("mcpServers", {})
    assert observed == {
        "old_servers": {"s1"},
        "new_servers": set(),
    }
    remount.assert_awaited_once_with(app, base_path="/mcp")


@pytest.mark.asyncio
async def test_remove_final_server_clears_mcp_proxy(tmp_path):
    app = await _build_app(
        tmp_path,
        servers={"s1": {"command": "echo", "args": ["ok"]}},
    )
    client = TestClient(app)

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    with patch("mcpo.main.reload_config_handler", side_effect=fake_reload):
        response = client.delete("/_meta/servers/s1")

    assert response.status_code == 200
    assert json.loads((tmp_path / "mcpo.json").read_text())["mcpServers"] == {}
    assert app.state.config_data["mcpServers"] == {}
    assert app.state.fastmcp_proxy_mounts == []
    assert app.state.fastmcp_proxy_global_mount is None


@pytest.mark.asyncio
async def test_config_save_rejects_invalid_structure_before_write(tmp_path):
    app = await _build_app(tmp_path)
    config_path = tmp_path / "mcpo.json"
    original = config_path.read_text()
    client = TestClient(app)

    response = client.post("/_meta/config/save", json={"content": "{}"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid"
    assert config_path.read_text() == original


@pytest.mark.asyncio
async def test_mcp_servers_save_validates_entries_before_write(tmp_path):
    app = await _build_app(tmp_path)
    config_path = tmp_path / "mcpo.json"
    original = config_path.read_text()
    client = TestClient(app)

    response = client.post(
        "/_meta/config/mcpServers/save",
        json={"data": {"broken": {}}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid"
    assert config_path.read_text() == original


@pytest.mark.asyncio
async def test_remove_server_not_found(tmp_path):
    app = await _build_app(tmp_path, servers={"s1": {"command": "echo", "args": ["ok"]}})
    client = TestClient(app)

    r = client.delete("/_meta/servers/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_remove_server_no_config_mode(tmp_path):
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app()
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.delete("/_meta/servers/s1")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "no_config_mode"


# ===========================================================================
# 15. Round-trip: disable server -> verify list reflects it -> re-enable
# ===========================================================================

@pytest.mark.asyncio
async def test_enable_disable_roundtrip(tmp_path):
    app = await _build_app(tmp_path)
    _mount_fake_server(app, "s1", tools=["ping"])
    client = TestClient(app)

    # Initially enabled
    r = client.get("/_meta/servers")
    servers = {s["name"]: s for s in r.json()["servers"]}
    assert servers["s1"]["enabled"] is True

    # Disable
    r = client.post("/_meta/servers/s1/disable")
    assert r.status_code == 200

    r = client.get("/_meta/servers")
    servers = {s["name"]: s for s in r.json()["servers"]}
    assert servers["s1"]["enabled"] is False

    # Re-enable
    with patch("mcpo.main.spawn_server_runtime", new=AsyncMock(return_value=SimpleNamespace(connected=True, last_error=None))):
        r = client.post("/_meta/servers/s1/enable")
    assert r.status_code == 200

    r = client.get("/_meta/servers")
    servers = {s["name"]: s for s in r.json()["servers"]}
    assert servers["s1"]["enabled"] is True


# ===========================================================================
# 16. Round-trip: disable tool -> verify tools list -> re-enable
# ===========================================================================

@pytest.mark.asyncio
async def test_tool_enable_disable_roundtrip(tmp_path):
    app = await _build_app(tmp_path, servers={})
    _mount_fake_server(app, "s1", tools=["alpha"])
    client = TestClient(app)

    # Disable tool
    r = client.post("/_meta/servers/s1/tools/alpha/disable")
    assert r.status_code == 200

    r = client.get("/_meta/servers/s1/tools")
    tools_map = {t["name"]: t for t in r.json()["tools"]}
    assert tools_map["alpha"]["enabled"] is False

    # Re-enable
    r = client.post("/_meta/servers/s1/tools/alpha/enable")
    assert r.status_code == 200

    r = client.get("/_meta/servers/s1/tools")
    tools_map = {t["name"]: t for t in r.json()["tools"]}
    assert tools_map["alpha"]["enabled"] is True


# ===========================================================================
# 17. All write endpoints blocked in read-only mode
# ===========================================================================

@pytest.mark.asyncio
async def test_all_writes_blocked_read_only(tmp_path):
    app = await _build_app(tmp_path, read_only=True)
    client = TestClient(app)

    write_paths = [
        ("POST", "/_meta/servers/s1/enable"),
        ("POST", "/_meta/servers/s1/disable"),
        ("POST", "/_meta/servers/s1/tools/t/enable"),
        ("POST", "/_meta/servers/s1/tools/t/disable"),
        ("POST", "/_meta/reload"),
        ("POST", "/_meta/reinit/s1"),
        ("POST", "/_meta/servers"),
    ]

    for method, path in write_paths:
        if method == "POST":
            if path == "/_meta/servers":
                r = client.post(path, json={"name": "x", "command": "echo"})
            else:
                r = client.post(path)
        assert r.status_code == 403, f"Expected 403 for {method} {path}, got {r.status_code}"
        body = r.json()
        assert body["ok"] is False, f"Expected ok=False for {method} {path}"
        assert body["error"]["code"] == "read_only", f"Expected read_only code for {method} {path}"


# ===========================================================================
# 18. GET /_meta/config/content — config file content
# ===========================================================================

@pytest.mark.asyncio
async def test_config_content_happy(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/_meta/config/content")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "content" in body
    # Content should be valid JSON matching our written config
    parsed = json.loads(body["content"])
    assert "mcpServers" in parsed


@pytest.mark.asyncio
async def test_config_content_no_config(tmp_path):
    sm = _fresh_state_manager(tmp_path)
    from mcpo.main import build_main_app
    with patch("mcpo.main.get_state_manager", return_value=sm):
        app = await build_main_app()
    app.state.state_manager = sm
    client = TestClient(app)

    r = client.get("/_meta/config/content")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False


# ===========================================================================
# 19. GET /_meta/logs/sources
# ===========================================================================

@pytest.mark.asyncio
async def test_log_sources(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/_meta/logs/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    sources = body["sources"]
    assert any(s["id"] == "openapi" for s in sources)


# ===========================================================================
# 20. GET /_meta/logs
# ===========================================================================

@pytest.mark.asyncio
async def test_logs_endpoint(tmp_path):
    app = await _build_app(tmp_path)
    client = TestClient(app)

    r = client.get("/_meta/logs")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["logs"], list)
    assert "nextCursor" in body
    assert "latestCursor" in body


# ===========================================================================
# 21. Metrics perTool details after a tool call
# ===========================================================================

@pytest.mark.asyncio
async def test_metrics_per_tool_after_call(tmp_path):
    """Tool calls via the handler use the MetricsAggregator singleton, not
    app.state.metrics.  Verify that calls succeed and the metrics endpoint
    still returns a valid structure (the in-memory dict may or may not reflect
    aggregator-level counts depending on wiring).
    """
    app = await _build_app(tmp_path, servers={})
    _mount_fake_server(app, "s1", tools=["demo"])
    client = TestClient(app)

    # Call the tool (no-arg handler)
    r = client.post("/s1/demo")
    assert r.status_code == 200

    # Check metrics endpoint returns valid structure
    r = client.get("/_meta/metrics")
    assert r.status_code == 200
    m = r.json()["metrics"]
    assert "calls" in m
    assert "errors" in m
    assert "perTool" in m
    # calls.total is populated from app.state.metrics which is a separate dict
    # from the MetricsAggregator; presence and type are sufficient here
    assert isinstance(m["calls"]["total"], int)


# ===========================================================================
# 22. Add server with URL (SSE/streamable-http type)
# ===========================================================================

@pytest.mark.asyncio
async def test_add_server_with_url(tmp_path):
    app = await _build_app(tmp_path, servers={})
    client = TestClient(app)

    async def fake_reload(app, cfg):
        pass

    remount = AsyncMock()
    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        r = client.post("/_meta/servers", json={
            "name": "remote1",
            "url": "http://example.com/sse",
            "type": "sse",
        })

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["server"] == "remote1"

    cfg = json.loads((tmp_path / "mcpo.json").read_text())
    assert cfg["mcpServers"]["remote1"]["url"] == "http://example.com/sse"
    assert cfg["mcpServers"]["remote1"]["type"] == "sse"
    remount.assert_awaited_once_with(app, base_path="/mcp")


# ===========================================================================
# 23. Add server with non-string command rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_add_server_command_not_string(tmp_path):
    app = await _build_app(tmp_path, servers={})
    client = TestClient(app)

    r = client.post("/_meta/servers", json={"name": "bad", "command": 123})
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid"

@pytest.mark.asyncio
async def test_internal_post_config_rejects_invalid_candidate_before_write(tmp_path):
    app = await _build_app(tmp_path)
    config_path = tmp_path / "mcpo.json"
    original_bytes = config_path.read_bytes()
    client = TestClient(app)

    reload_mock = AsyncMock()
    remount = AsyncMock()
    with (
        patch("mcpo.main.reload_config_handler", new=reload_mock),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = client.post(
            "/mcpo/post_config",
            json={"config": {"mcpServers": {"broken": {}}}},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid"
    assert config_path.read_bytes() == original_bytes
    reload_mock.assert_not_awaited()
    remount.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_post_config_remount_failure_rolls_back_file_and_state(tmp_path):
    app = await _build_app(tmp_path)
    config_path = tmp_path / "mcpo.json"
    original_bytes = config_path.read_bytes()
    original_config = json.loads(json.dumps(app.state.config_data))
    client = TestClient(app)

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    reload_mock = AsyncMock(side_effect=fake_reload)
    remount = AsyncMock(side_effect=[RuntimeError("remount failed"), None])
    with (
        patch("mcpo.main.reload_config_handler", new=reload_mock),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = client.post(
            "/mcpo/post_config",
            json={"config": {"mcpServers": {"next": {"command": "echo"}}}},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "reload_failed"
    assert config_path.read_bytes() == original_bytes
    assert app.state.config_data == original_config
    assert reload_mock.await_count == 2
    assert remount.await_count == 2


@pytest.mark.asyncio
async def test_config_activation_failure_rebuilds_torn_down_runtime(tmp_path):
    import mcpo.main as main_module

    app = await _build_app(
        tmp_path,
        servers={"s1": {"command": "echo", "args": ["old"]}},
    )
    config_path = tmp_path / "mcpo.json"
    original_bytes = config_path.read_bytes()
    original_config = json.loads(json.dumps(app.state.config_data))
    candidate = {
        "mcpServers": {
            "s1": {"command": "echo", "args": ["candidate"]}
        }
    }
    main_module._atomic_write_config(
        str(config_path),
        json.dumps(candidate, indent=2),
    )

    old_route = main_module._find_server_mounts(app, "s1")[0]
    old_runtime = SimpleNamespace(
        connected=True,
        last_error=None,
        task=SimpleNamespace(done=lambda: False),
        bound_app=old_route.app,
    )
    app.state.server_runtimes = {"s1": old_runtime}
    state = app.state.state_manager
    original_set_enabled = state.set_server_enabled
    failed = False

    def fail_candidate_activation(server_name, enabled):
        nonlocal failed
        if server_name == "s1" and not failed:
            failed = True
            raise RuntimeError("candidate activation failed")
        return original_set_enabled(server_name, enabled)

    async def teardown_runtime(target, server_name):
        target.state.server_runtimes.pop(server_name, None)
        return {
            "ok": True,
            "server": server_name,
            "was_running": True,
        }

    async def spawn_runtime(target, server_name, sub_app, **kwargs):
        runtime = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
            bound_app=sub_app,
        )
        target.state.server_runtimes[server_name] = runtime
        return runtime

    teardown = AsyncMock(side_effect=teardown_runtime)
    spawn = AsyncMock(side_effect=spawn_runtime)
    remount = AsyncMock()
    with (
        patch("mcpo.main.get_state_manager", return_value=state),
        patch.object(
            state,
            "set_server_enabled",
            side_effect=fail_candidate_activation,
        ),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
        pytest.raises(RuntimeError, match="candidate activation failed"),
    ):
        await main_module._reload_config_with_rollback(
            app,
            candidate,
            original_config,
            str(config_path),
            original_bytes,
        )

    restored_route = main_module._find_server_mounts(app, "s1")[0]
    restored_runtime = app.state.server_runtimes["s1"]
    assert config_path.read_bytes() == original_bytes
    assert app.state.config_data == original_config
    assert restored_runtime is not old_runtime
    assert restored_runtime.bound_app is restored_route.app
    assert restored_runtime.task.done() is False
    assert len(main_module._find_server_mounts(app, "s1")) == 1
    teardown.assert_awaited_once()
    spawn.assert_awaited_once()
    remount.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_candidate_creation_failure_retains_live_generation(
    tmp_path,
):
    import mcpo.main as main_module

    app = await _build_app(
        tmp_path,
        servers={"s1": {"command": "echo", "args": ["old"]}},
    )
    original_config = json.loads(json.dumps(app.state.config_data))
    old_route = main_module._find_server_mounts(app, "s1")[0]
    old_runtime = SimpleNamespace(
        connected=True,
        last_error=None,
        task=SimpleNamespace(done=lambda: False),
        bound_app=old_route.app,
    )
    app.state.server_runtimes = {"s1": old_runtime}
    app.state.fastmcp_proxy_mounts = []
    candidate = {
        "mcpServers": {
            "s1": {"command": "echo", "args": ["broken"]}
        }
    }
    original_create = main_module.create_sub_app

    def fail_candidate(server_name, server_cfg, *args, **kwargs):
        if server_cfg.get("args") == ["broken"]:
            raise RuntimeError("candidate creation failed")
        return original_create(server_name, server_cfg, *args, **kwargs)

    teardown = AsyncMock()
    spawn = AsyncMock()
    remount = AsyncMock()
    with (
        patch(
            "mcpo.main.get_state_manager",
            return_value=app.state.state_manager,
        ),
        patch("mcpo.main.create_sub_app", side_effect=fail_candidate),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
        pytest.raises(RuntimeError, match="candidate creation failed"),
    ):
        await main_module._reload_runtime_surfaces_with_rollback(
            app,
            candidate,
        )

    assert app.state.config_data == original_config
    assert main_module._find_server_mounts(app, "s1") == [old_route]
    assert app.state.server_runtimes["s1"] is old_runtime
    teardown.assert_not_awaited()
    spawn.assert_not_awaited()
    remount.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_reload_failure_restores_exact_previous_generation(
    tmp_path,
):
    import mcpo.main as main_module
    from starlette.routing import Mount

    old_servers = {
        "keep": {"command": "echo", "args": ["keep"]},
        "remove": {"command": "echo", "args": ["remove"]},
        "update": {"command": "echo", "args": ["old"]},
    }
    app = await _build_app(tmp_path, servers=old_servers)
    original_config = json.loads(json.dumps(app.state.config_data))
    old_routes = {
        name: main_module._find_server_mounts(app, name)[0]
        for name in old_servers
    }
    old_runtimes = {
        name: SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
            bound_app=old_routes[name].app,
        )
        for name in old_servers
    }
    app.state.server_runtimes = dict(old_runtimes)
    candidate = {
        "mcpServers": {
            "keep": old_servers["keep"],
            "add": {"command": "echo", "args": ["add"]},
            "update": {"command": "echo", "args": ["new"]},
        }
    }
    state = app.state.state_manager
    original_set_enabled = state.set_server_enabled
    failed = False

    def fail_update_activation(server_name, enabled):
        nonlocal failed
        if server_name == "update" and not failed:
            failed = True
            raise RuntimeError("update activation failed")
        return original_set_enabled(server_name, enabled)

    teardown_names = []

    async def teardown_runtime(target, server_name):
        teardown_names.append(server_name)
        target.state.server_runtimes.pop(server_name, None)
        return {"ok": True, "server": server_name}

    spawn_names = []

    async def spawn_runtime(target, server_name, sub_app, **kwargs):
        spawn_names.append(server_name)
        runtime = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
            bound_app=sub_app,
        )
        target.state.server_runtimes[server_name] = runtime
        return runtime

    with (
        patch("mcpo.main.get_state_manager", return_value=state),
        patch.object(
            state,
            "set_server_enabled",
            side_effect=fail_update_activation,
        ),
        patch(
            "mcpo.main.teardown_server_runtime",
            side_effect=teardown_runtime,
        ),
        patch(
            "mcpo.main.spawn_server_runtime",
            side_effect=spawn_runtime,
        ),
        pytest.raises(RuntimeError, match="update activation failed"),
    ):
        await main_module.reload_config_handler(app, candidate)

    routes = {
        main_module._server_name_from_mount(route): route
        for route in app.router.routes
        if isinstance(route, Mount)
        and main_module._server_name_from_mount(route) in old_servers
    }
    assert app.state.config_data == original_config
    assert set(routes) == set(old_servers)
    assert set(app.state.server_runtimes) == set(old_servers)
    assert routes["keep"] is old_routes["keep"]
    assert app.state.server_runtimes["keep"] is old_runtimes["keep"]
    for name in ("remove", "update"):
        assert app.state.server_runtimes[name] is not old_runtimes[name]
        assert app.state.server_runtimes[name].bound_app is routes[name].app
    assert all(
        len(main_module._find_server_mounts(app, name)) == 1
        for name in old_servers
    )
    assert teardown_names == ["remove", "update", "add"]
    assert spawn_names == ["add", "remove", "update"]


@pytest.mark.asyncio
async def test_partial_teardown_failure_rebuilds_only_stopped_server(
    tmp_path,
):
    import mcpo.main as main_module

    old_servers = {
        "first": {"command": "echo", "args": ["first"]},
        "second": {"command": "echo", "args": ["second"]},
    }
    app = await _build_app(tmp_path, servers=old_servers)
    original_config = json.loads(json.dumps(app.state.config_data))
    old_routes = {
        name: main_module._find_server_mounts(app, name)[0]
        for name in old_servers
    }
    old_runtimes = {
        name: SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
            bound_app=old_routes[name].app,
        )
        for name in old_servers
    }
    app.state.server_runtimes = dict(old_runtimes)

    async def teardown_runtime(target, server_name):
        if server_name == "first":
            target.state.server_runtimes.pop(server_name, None)
            return {"ok": True, "server": server_name}
        return {
            "ok": False,
            "server": server_name,
            "error": "still running",
        }

    async def spawn_runtime(target, server_name, sub_app, **kwargs):
        runtime = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
            bound_app=sub_app,
        )
        target.state.server_runtimes[server_name] = runtime
        return runtime

    spawn = AsyncMock(side_effect=spawn_runtime)
    with (
        patch(
            "mcpo.main.get_state_manager",
            return_value=app.state.state_manager,
        ),
        patch(
            "mcpo.main.teardown_server_runtime",
            side_effect=teardown_runtime,
        ),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        pytest.raises(RuntimeError, match="still running"),
    ):
        await main_module.reload_config_handler(
            app,
            {"mcpServers": {}},
        )

    first_route = main_module._find_server_mounts(app, "first")[0]
    assert app.state.config_data == original_config
    assert len(main_module._find_server_mounts(app, "first")) == 1
    assert len(main_module._find_server_mounts(app, "second")) == 1
    assert app.state.server_runtimes["first"] is not old_runtimes["first"]
    assert app.state.server_runtimes["first"].bound_app is first_route.app
    assert app.state.server_runtimes["second"] is old_runtimes["second"]
    assert main_module._find_server_mounts(app, "second") == [
        old_routes["second"]
    ]
    spawn.assert_awaited_once()

@pytest.mark.asyncio
async def test_enable_state_save_failure_has_no_runtime_or_route_side_effect(tmp_path):
    from mcpo.services.state import StateSaveError

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    state.set_server_enabled("s1", False)
    from mcpo.main import _remove_server_mounts
    _remove_server_mounts(app, "s1")
    client = TestClient(app)
    spawn = AsyncMock()
    teardown = AsyncMock()

    with (
        patch.object(state, "set_server_enabled", side_effect=StateSaveError("disk full")),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
    ):
        response = client.post("/_meta/servers/s1/enable")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "state_save_failed"
    assert state.is_server_enabled("s1") is False
    assert not any(getattr(route, "path", None) == "/s1" for route in app.router.routes)
    spawn.assert_not_awaited()
    teardown.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_state_save_failure_has_no_runtime_or_route_side_effect(tmp_path):
    from mcpo.services.state import StateSaveError

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    original_route = next(
        route for route in app.router.routes if getattr(route, "path", None) == "/s1"
    )
    client = TestClient(app)
    teardown = AsyncMock()

    with (
        patch.object(state, "set_server_enabled", side_effect=StateSaveError("disk full")),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
    ):
        response = client.post("/_meta/servers/s1/disable")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "state_save_failed"
    assert state.is_server_enabled("s1") is True
    assert next(
        route for route in app.router.routes if getattr(route, "path", None) == "/s1"
    ) is original_route
    teardown.assert_not_awaited()

@pytest.mark.asyncio
async def test_repeated_enable_keeps_live_runtime_bound_to_original_mount(tmp_path):
    app = await _build_app(tmp_path)
    original_route = next(
        route for route in app.router.routes if getattr(route, "path", None) == "/s1"
    )
    runtime = SimpleNamespace(
        connected=True,
        last_error=None,
        task=SimpleNamespace(done=lambda: False),
    )
    app.state.server_runtimes = {"s1": runtime}
    app.state.fastmcp_proxy_mounts = []
    spawn = AsyncMock()
    remount = AsyncMock()

    with (
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = TestClient(app).post("/_meta/servers/s1/enable")

    assert response.status_code == 200
    assert next(
        route for route in app.router.routes if getattr(route, "path", None) == "/s1"
    ) is original_route
    assert app.state.server_runtimes["s1"] is runtime
    spawn.assert_not_awaited()
    remount.assert_awaited_once_with(app, base_path="/mcp")


@pytest.mark.asyncio
async def test_disable_teardown_failure_restores_a_connected_runtime(tmp_path):
    app = await _build_app(tmp_path)
    state = app.state.state_manager
    prior_runtime = SimpleNamespace(
        connected=True,
        last_error=None,
        task=SimpleNamespace(done=lambda: False),
    )
    app.state.server_runtimes = {"s1": prior_runtime}

    async def fail_teardown(target, server_name):
        target.state.server_runtimes.pop(server_name, None)
        return {"ok": False, "server": server_name, "error": "did not stop"}

    async def restore_runtime(target, server_name, sub_app, **kwargs):
        restored = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
        )
        target.state.server_runtimes[server_name] = restored
        return restored

    with (
        patch("mcpo.main.teardown_server_runtime", side_effect=fail_teardown),
        patch("mcpo.main.spawn_server_runtime", side_effect=restore_runtime),
    ):
        response = TestClient(app).post("/_meta/servers/s1/disable")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "teardown_failed"
    assert state.is_server_enabled("s1") is True
    assert app.state.server_runtimes["s1"].connected is True
    assert len(
        [route for route in app.router.routes if getattr(route, "path", None) == "/s1"]
    ) == 1

@pytest.mark.asyncio
async def test_disable_syncs_initialized_native_proxy(tmp_path):
    app = await _build_app(tmp_path)
    app.state.fastmcp_proxy_mounts = []
    remount = AsyncMock()

    with patch("mcpo.main._mount_or_remount_fastmcp", new=remount):
        response = TestClient(app).post("/_meta/servers/s1/disable")

    assert response.status_code == 200
    assert app.state.state_manager.is_server_enabled("s1") is False
    remount.assert_awaited_once_with(app, base_path="/mcp")


@pytest.mark.asyncio
async def test_enable_native_remount_failure_rolls_back_runtime_route_and_state(
    tmp_path,
):
    import mcpo.main as main_module

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    state.set_server_enabled("s1", False)
    main_module._remove_server_mounts(app, "s1")
    app.state.fastmcp_proxy_mounts = []

    spawn = AsyncMock(
        return_value=SimpleNamespace(connected=True, last_error=None)
    )
    teardown = AsyncMock(return_value={"ok": True})
    remount = AsyncMock(side_effect=[RuntimeError("native remount failed"), None])

    with (
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = TestClient(app).post("/_meta/servers/s1/enable")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "native_remount_failed"
    assert state.is_server_enabled("s1") is False
    assert main_module._find_server_mounts(app, "s1") == []
    spawn.assert_awaited_once()
    teardown.assert_awaited_once_with(app, "s1")
    assert remount.await_count == 2


@pytest.mark.asyncio
async def test_disable_native_remount_failure_restores_server_state_and_runtime(
    tmp_path,
):
    import mcpo.main as main_module

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    app.state.fastmcp_proxy_mounts = []
    teardown = AsyncMock(return_value={"ok": True, "was_running": False})

    async def restore_runtime(target, server_name, sub_app, **kwargs):
        restored = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
        )
        target.state.server_runtimes[server_name] = restored
        return restored

    spawn = AsyncMock(side_effect=restore_runtime)
    remount = AsyncMock(side_effect=[RuntimeError("native remount failed"), None])

    with (
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main.spawn_server_runtime", new=spawn),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = TestClient(app).post("/_meta/servers/s1/disable")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "native_remount_failed"
    assert state.is_server_enabled("s1") is True
    assert app.state.server_runtimes["s1"].connected is True
    assert len(main_module._find_server_mounts(app, "s1")) == 1
    spawn.assert_awaited_once()
    assert remount.await_count == 2


@pytest.mark.asyncio
async def test_wrapped_add_preserves_single_nested_mcp_servers_owner(tmp_path):
    app = await _build_wrapped_app(
        tmp_path,
        servers={"base": {"command": "echo", "args": ["base"]}},
    )

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=AsyncMock()),
    ):
        response = TestClient(app).post(
            "/_meta/servers",
            json={"name": "added", "command": "echo added"},
        )

    raw = json.loads((tmp_path / "mcpo.json").read_text())
    assert response.status_code == 200
    assert "mcpServers" not in raw
    assert set(raw["config"]["mcpServers"]) == {"base", "added"}
    assert raw["config"]["keep"] == "unchanged"


@pytest.mark.asyncio
async def test_wrapped_remove_preserves_single_nested_mcp_servers_owner(tmp_path):
    app = await _build_wrapped_app(
        tmp_path,
        servers={
            "base": {"command": "echo", "args": ["base"]},
            "remove": {"command": "echo", "args": ["remove"]},
        },
    )

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=AsyncMock()),
    ):
        response = TestClient(app).delete("/_meta/servers/remove")

    raw = json.loads((tmp_path / "mcpo.json").read_text())
    assert response.status_code == 200
    assert "mcpServers" not in raw
    assert set(raw["config"]["mcpServers"]) == {"base"}
    assert raw["config"]["keep"] == "unchanged"


@pytest.mark.asyncio
async def test_wrapped_partial_save_and_get_use_nested_mcp_servers(tmp_path):
    app = await _build_wrapped_app(tmp_path)

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=AsyncMock()),
    ):
        client = TestClient(app)
        before = client.get("/_meta/config/mcpServers")
        response = client.post(
            "/_meta/config/mcpServers/save",
            json={"data": {"next": {"command": "echo", "args": ["next"]}}},
        )

    raw = json.loads((tmp_path / "mcpo.json").read_text())
    assert json.loads(before.json()["content"]) == {
        "s1": {"command": "echo", "args": ["ok"]}
    }
    assert response.status_code == 200
    assert "mcpServers" not in raw
    assert set(raw["config"]["mcpServers"]) == {"next"}
    assert raw["config"]["keep"] == "unchanged"


@pytest.mark.asyncio
async def test_concurrent_add_server_transactions_do_not_lose_updates(tmp_path):
    app = await _build_app(tmp_path, servers={})

    async def delayed_reload(target, cfg):
        await asyncio.sleep(0.03)
        target.state.config_data = cfg

    transport = httpx.ASGITransport(app=app)
    with (
        patch("mcpo.main.reload_config_handler", side_effect=delayed_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=AsyncMock()),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            responses = await asyncio.gather(
                client.post(
                    "/_meta/servers",
                    json={"name": "alpha", "command": "echo alpha"},
                ),
                client.post(
                    "/_meta/servers",
                    json={"name": "beta", "command": "echo beta"},
                ),
            )

    assert [response.status_code for response in responses] == [200, 200]
    raw = json.loads((tmp_path / "mcpo.json").read_text())
    assert set(raw["mcpServers"]) == {"alpha", "beta"}
    assert set(app.state.config_data["mcpServers"]) == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_failed_post_config_cannot_erase_newer_successful_transaction(tmp_path):
    app = await _build_app(tmp_path, servers={})
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(json.dumps({"marker": "old", "mcpServers": {}}))
    app.state.config_data = {"marker": "old", "mcpServers": {}}

    first_remount_entered = asyncio.Event()
    release_first_remount = asyncio.Event()

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    async def controlled_remount(target, *, base_path):
        marker = target.state.config_data.get("marker")
        if marker == "A":
            first_remount_entered.set()
            await release_first_remount.wait()
            raise RuntimeError("candidate A failed")

    transport = httpx.ASGITransport(app=app)
    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch(
            "mcpo.main._mount_or_remount_fastmcp",
            side_effect=controlled_remount,
        ),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/mcpo/post_config",
                    json={"config": {"marker": "A", "mcpServers": {}}},
                )
            )
            await first_remount_entered.wait()
            second = asyncio.create_task(
                client.post(
                    "/mcpo/post_config",
                    json={"config": {"marker": "B", "mcpServers": {}}},
                )
            )
            await asyncio.sleep(0.05)
            release_first_remount.set()
            first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 500
    assert second_response.status_code == 200
    assert json.loads(config_path.read_text())["marker"] == "B"
    assert app.state.config_data["marker"] == "B"


@pytest.mark.asyncio
async def test_explicit_reload_syncs_initialized_native_proxy(tmp_path):
    app = await _build_app(tmp_path)
    app.state.fastmcp_proxy_mounts = []
    remount = AsyncMock()

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = TestClient(app).post("/_meta/reload")

    assert response.status_code == 200
    remount.assert_awaited_once_with(app, base_path="/mcp")


@pytest.mark.asyncio
async def test_explicit_reload_remount_failure_restores_runtime_not_file(tmp_path):
    app = await _build_app(tmp_path)
    app.state.fastmcp_proxy_mounts = []
    config_path = tmp_path / "mcpo.json"
    original_config = json.loads(json.dumps(app.state.config_data))
    candidate = {
        "marker": "external",
        "mcpServers": {"next": {"command": "echo", "args": ["next"]}},
    }
    config_path.write_text(json.dumps(candidate))

    async def fake_reload(target, cfg):
        target.state.config_data = cfg

    remount = AsyncMock(side_effect=[RuntimeError("native failed"), None])
    with (
        patch("mcpo.main.reload_config_handler", side_effect=fake_reload),
        patch("mcpo.main._mount_or_remount_fastmcp", new=remount),
    ):
        response = TestClient(app).post("/_meta/reload")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "reload_failed"
    assert app.state.config_data == original_config
    assert json.loads(config_path.read_text()) == candidate
    assert remount.await_count == 2


@pytest.mark.asyncio
async def test_state_save_errors_have_specific_envelope_for_all_toggle_surfaces(tmp_path):
    from mcpo.services.state import StateSaveError

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    client = TestClient(app)

    with patch.object(
        state,
        "set_tool_enabled",
        side_effect=StateSaveError("disk full"),
    ):
        tool = client.post("/_meta/servers/s1/tools/ping/disable")
    with patch.object(
        state,
        "set_rest_tools_enabled",
        side_effect=StateSaveError("disk full"),
    ):
        rest = client.post("/_meta/rest-tools/disable")
    with (
        patch(
            "mcpo.api.routers.admin.get_state_manager",
            return_value=state,
        ),
        patch.object(
            state,
            "set_code_mode_enabled",
            side_effect=StateSaveError("disk full"),
        ),
    ):
        code_mode = client.post("/_meta/code-mode", json={"enabled": True})

    for response in (tool, rest, code_mode):
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "state_save_failed"


@pytest.mark.asyncio
async def test_disable_double_native_remount_failure_is_fail_closed(tmp_path):
    import mcpo.main as main_module

    app = await _build_app(tmp_path)
    state = app.state.state_manager
    app.state.fastmcp_proxy_mounts = []
    teardown = AsyncMock(return_value={"ok": True, "was_running": False})

    async def restore_runtime(target, server_name, sub_app, **kwargs):
        restored = SimpleNamespace(
            connected=True,
            last_error=None,
            task=SimpleNamespace(done=lambda: False),
        )
        target.state.server_runtimes[server_name] = restored
        return restored

    with (
        patch("mcpo.main.teardown_server_runtime", new=teardown),
        patch("mcpo.main.spawn_server_runtime", side_effect=restore_runtime),
        patch(
            "mcpo.main._mount_or_remount_fastmcp",
            new=AsyncMock(
                side_effect=[
                    RuntimeError("candidate remount failed"),
                    RuntimeError("rollback remount failed"),
                ]
            ),
        ),
    ):
        response = TestClient(app).post("/_meta/servers/s1/disable")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "state_rollback_failed"
    assert state.is_server_enabled("s1") is False
    assert main_module._find_server_mounts(app, "s1") == []
    assert "s1" not in getattr(app.state, "server_runtimes", {})
