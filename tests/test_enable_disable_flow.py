from fastapi.testclient import TestClient
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import mcpo.services.state as _state_mod


@pytest.mark.asyncio
async def test_enable_disable_server_via_meta_endpoints(tmp_path, monkeypatch):
    """Test server enable/disable via /_meta/servers/{name}/enable|disable endpoints."""
    from mcpo.main import build_main_app

    # Reset the global singleton so a fresh StateManager is created with a
    # temporary state file (avoids leaking state from mcpo_state.json).
    monkeypatch.setattr(_state_mod, '_global_state_manager', None)

    state_file = tmp_path / 'state.json'
    monkeypatch.setattr(_state_mod, '_global_state_manager', _state_mod.StateManager(str(state_file)))
    cfg_path = tmp_path / 'mcpo.json'
    cfg_path.write_text(json.dumps({"mcpServers": {"s1": {"command": "echo", "args": ["ok"]}}}))

    app = await build_main_app(config_path=str(cfg_path))
    client = TestClient(app)

    state_manager = _state_mod.get_state_manager()

    # Disable server via endpoint
    r = client.post('/_meta/servers/s1/disable')
    assert r.status_code == 200 and r.json().get('enabled') is False
    assert client.get('/s1/openapi.json').status_code == 404
    listed = {item['name']: item for item in client.get('/_meta/servers').json()['servers']}
    assert listed['s1']['enabled'] is False and listed['s1']['connected'] is False

    # Verify via state manager
    assert state_manager.is_server_enabled('s1') is False

    # Re-enable server via endpoint while REST exposure is off. The server
    # runtime lifecycle is independent from the global REST tool surface.
    state_manager.set_rest_tools_enabled(False)
    spawn_mock = AsyncMock(
        return_value=SimpleNamespace(connected=True, last_error=None)
    )
    with patch('mcpo.main.spawn_server_runtime', new=spawn_mock):
        r = client.post('/_meta/servers/s1/enable')
    assert r.status_code == 200 and r.json().get('enabled') is True
    spawn_mock.assert_awaited_once()

    # Verify via state manager
    assert state_manager.is_server_enabled('s1') is True
    assert client.get('/s1/openapi.json').status_code == 200


@pytest.mark.asyncio
async def test_disabled_tool_returns_403(monkeypatch, tmp_path):
    """Test that calling a disabled tool returns 403."""
    from mcpo.main import build_main_app
    from mcpo.utils.main import get_tool_handler
    from fastapi import FastAPI

    class FakeResult:
        isError = False
        content = []

    class FakeSession:
        async def call_tool(self, name, arguments):
            return FakeResult()

    # Build app and sub-app manually to attach handler
    app = await build_main_app()
    sub = FastAPI(title='sX')
    sub.state.parent_app = app
    sub.state.session = FakeSession()

    # Register a tool handler named 'demo'
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None)
    sub.post('/demo')(handler)
    app.mount('/sX', sub)

    # Set up legacy tool_enabled dict on app.state for handler compatibility
    app.state.tool_enabled = {'sX': {'demo': False}}

    client = TestClient(app)
    resp = client.post('/sX/demo')
    assert resp.status_code == 403
    js = resp.json()
    # The response may be wrapped in error_envelope ({"ok": False, ...}) if the
    # main_app exception handler intercepts, or as FastAPI's default
    # {"detail": {"message": ..., "code": ...}} if the sub-app handles it.
    # Either way, 403 confirms the tool is blocked.
    if 'ok' in js:
        assert js['ok'] is False
    elif 'detail' in js:
        detail = js['detail']
        if isinstance(detail, dict):
            assert detail.get('code') == 'disabled' or 'disabled' in detail.get('message', '').lower()


@pytest.mark.asyncio
async def test_rest_toggle_preserves_runtimes_and_server_tool_state(
    monkeypatch,
    tmp_path,
):
    from mcpo.main import build_main_app

    state = _state_mod.StateManager(str(tmp_path / "state.json"))
    monkeypatch.setattr(_state_mod, '_global_state_manager', state)
    app = await build_main_app()
    sentinel_runtime = object()
    app.state.server_runtimes = {"alpha": sentinel_runtime}
    state.set_server_enabled("alpha", True)
    state.set_tool_enabled("alpha", "danger", False)
    client = TestClient(app)

    spawn_mock = AsyncMock()
    teardown_mock = AsyncMock()
    with (
        patch("mcpo.main.spawn_server_runtime", new=spawn_mock),
        patch("mcpo.main.teardown_server_runtime", new=teardown_mock),
    ):
        disabled = client.post("/_meta/rest-tools/disable")
        enabled = client.post("/_meta/rest-tools/enable")

    assert disabled.json() == {"ok": True, "enabled": False}
    assert enabled.json() == {"ok": True, "enabled": True}
    assert app.state.server_runtimes["alpha"] is sentinel_runtime
    assert state.is_server_enabled("alpha") is True
    assert state.is_tool_enabled("alpha", "danger") is False
    spawn_mock.assert_not_awaited()
    teardown_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_rest_disabled_blocks_tool_call_without_calling_session(
    monkeypatch,
    tmp_path,
):
    from fastapi import FastAPI
    from mcpo.main import build_main_app
    from mcpo.utils.main import get_tool_handler

    state = _state_mod.StateManager(str(tmp_path / "state.json"))
    monkeypatch.setattr(_state_mod, '_global_state_manager', state)
    app = await build_main_app()
    session = SimpleNamespace(call_tool=AsyncMock())
    sub = FastAPI(title="alpha")
    sub.state.parent_app = app
    sub.state.session = session
    sub.post("/demo")(
        get_tool_handler(sub.state.session, "demo", form_model_fields=None)
    )
    app.mount("/alpha", sub)
    state.set_rest_tools_enabled(False)

    response = TestClient(app).post("/alpha/demo")

    assert response.status_code == 403
    session.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_connects_enabled_server_when_rest_is_disabled(tmp_path):
    from fastapi import FastAPI
    from mcpo.main import lifespan

    state = _state_mod.StateManager(str(tmp_path / "state.json"))
    state.set_rest_tools_enabled(False)
    app = FastAPI(description="test")
    app.state.config_data = {
        "mcpServers": {"alpha": {"command": "echo", "args": ["ok"]}}
    }
    sub = FastAPI(title="alpha")
    app.mount("/alpha", sub)
    runtime = SimpleNamespace(connected=True, last_error=None)
    spawn_mock = AsyncMock(return_value=runtime)

    with (
        patch("mcpo.main.get_state_manager", return_value=state),
        patch("mcpo.main.spawn_server_runtime", new=spawn_mock),
    ):
        async with lifespan(app):
            pass

    spawn_mock.assert_awaited_once_with(
        app,
        "alpha",
        sub,
        api_dependency=None,
    )

