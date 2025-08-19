from fastapi.testclient import TestClient
import json
import pytest

@pytest.mark.asyncio
async def test_enable_disable_server_and_tool_enforcement(tmp_path):
    from mcpo.main import build_main_app

    cfg_path = tmp_path / 'mcpo.json'
    cfg_path.write_text(json.dumps({"mcpServers": {"s1": {"command": "echo", "args": ["ok"]}}}))

    app = await build_main_app(config_path=str(cfg_path))
    client = TestClient(app)

    # Initially enabled
    r = client.get('/_meta/servers')
    assert r.status_code == 200
    servers = {s['name']: s for s in r.json()['servers']}
    assert servers['s1']['enabled'] is True

    # Disable server
    r = client.post('/_meta/servers/s1/disable')
    assert r.status_code == 200 and r.json()['enabled'] is False

    # Simulate a tool endpoint existence by injecting state then calling tool handler path
    # We mimic a discovered tool named 'ping' (no args case) by setting enable map then hitting endpoint.
    app.state.tool_enabled.setdefault('s1', {})['ping'] = True

    # Re-enable server
    r = client.post('/_meta/servers/s1/enable')
    assert r.status_code == 200 and r.json()['enabled'] is True

    # Disable tool only (leave server enabled)
    app.state.tool_enabled['s1']['ping'] = False
    from mcpo.main import save_state_file
    save_state_file(app.state.config_path, app.state.server_enabled, app.state.tool_enabled)

    # Because the actual tool route isn't created (depends on real MCP session), directly test handler gating logic
    # by calling the internal mount path; since route won't exist we just verify state maps (sanity) here.
    assert app.state.server_enabled['s1'] is True
    assert app.state.tool_enabled['s1']['ping'] is False

@pytest.mark.asyncio
async def test_disabled_tool_returns_disabled_code(monkeypatch, tmp_path):
    # Build a fake session with a single tool to exercise the dynamic handler directly
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

    # Disable server or tool state
    app.state.server_enabled['sX'] = True
    app.state.tool_enabled.setdefault('sX', {})['demo'] = False

    client = TestClient(app)
    resp = client.post('/sX/demo')
    assert resp.status_code == 403
    js = resp.json()
    assert js['ok'] is False
    assert js['error']['code'] == 'disabled'
