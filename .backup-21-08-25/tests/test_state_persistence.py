from fastapi.testclient import TestClient
import json
import os
import tempfile
import asyncio
import pytest

@pytest.mark.asyncio
async def test_enable_disable_persists_state(tmp_path):
    from mcpo.main import build_main_app

    # Create a dummy config file
    cfg_path = tmp_path / 'mcpo.json'
    cfg_path.write_text(json.dumps({"mcpServers": {"s1": {"command": "echo", "args": ["ok"]}}}))

    app = await build_main_app(config_path=str(cfg_path))
    client = TestClient(app)

    # Disable server and tool state (server appears initially enabled; simulate enabling/disabling)
    r = client.post('/_meta/servers/s1/disable')
    assert r.status_code == 200 and r.json()['enabled'] is False

    # Simulate a tool existing by crafting state directly then persisting
    app.state.tool_enabled.setdefault('s1', {})['t1'] = False
    from mcpo.main import save_state_file
    save_state_file(app.state.config_path, app.state.server_enabled, app.state.tool_enabled)

    # Reload new app instance and ensure state loaded
    app2 = await build_main_app(config_path=str(cfg_path))
    assert app2.state.server_enabled.get('s1') is False
    assert app2.state.tool_enabled.get('s1', {}).get('t1') is False

@pytest.mark.asyncio
async def test_state_file_version_and_atomic(tmp_path):
    from mcpo.main import save_state_file, load_state_file, STATE_FILE_VERSION

    cfg_path = tmp_path / 'mcpo.json'
    cfg_path.write_text('{}')

    save_state_file(str(cfg_path), {'sA': True}, {'sA': {'toolX': True}})
    state_path = str(cfg_path).replace('.json', '') + '_state.json'
    data = json.loads(open(state_path).read())
    assert data['version'] == STATE_FILE_VERSION
    # Corrupt temp file should not break load
    tmp_corrupt = state_path + '.tmp'
    with open(tmp_corrupt, 'w') as f:
        f.write('{ not json')
    loaded = load_state_file(str(cfg_path))
    assert 'server_enabled' in loaded and 'tool_enabled' in loaded

@pytest.mark.asyncio
async def test_read_only_flag_blocks_mutations(tmp_path):
    from mcpo.main import build_main_app

    cfg_path = tmp_path / 'mcpo.json'
    cfg_path.write_text(json.dumps({"mcpServers": {"s1": {"command": "echo", "args": ["ok"]}}}))

    app = await build_main_app(config_path=str(cfg_path), read_only=True)
    client = TestClient(app)

    for path in [
        '/_meta/servers/s1/enable',
        '/_meta/servers/s1/disable',
        '/_meta/servers/s1/tools/x/enable',
        '/_meta/servers/s1/tools/x/disable',
        '/_meta/reload',
        '/_meta/reinit/s1',
        '/_meta/servers',
    ]:
        resp = client.post(path)
        assert resp.status_code == 403, path
        js = resp.json()
        assert js['ok'] is False and js['error']['code'] == 'read_only'

@pytest.mark.asyncio
async def test_metrics_stub_counts_disabled_and_timeout(tmp_path, monkeypatch):
    from mcpo.main import build_main_app
    from mcpo.utils.main import get_tool_handler
    from fastapi import FastAPI

    class FakeTimeoutSession:
        async def call_tool(self, name, arguments):
            await asyncio.sleep(0.01)
            # Force long sleep to trigger timeout externally by setting very small timeout
            return type('R', (), {'isError': False, 'content': []})

    # Build app and lower default timeout to force timeout scenario easily via override
    app = await build_main_app()
    sub = FastAPI(title='sM')
    sub.state.parent_app = app
    sub.state.session = FakeTimeoutSession()
    # Short timeout for test via state override
    sub.state.tool_timeout = 1
    sub.state.tool_timeout_max = 1
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None)
    sub.post('/demo')(handler)
    app.mount('/sM', sub)
    app.state.server_enabled['sM'] = True
    app.state.tool_enabled.setdefault('sM', {})['demo'] = False  # disabled first

    client = TestClient(app)
    # Disabled call
    r = client.post('/sM/demo')
    assert r.status_code == 403
    # Enable tool, then force timeout with override above max to get invalid first
    app.state.tool_enabled['sM']['demo'] = True
    r = client.post('/sM/demo', headers={'X-Tool-Timeout': '2'})  # above max -> invalid_timeout
    assert r.status_code == 400
    # Use valid timeout but cause actual call (should succeed quickly)
    r = client.post('/sM/demo')
    assert r.status_code == 200

    m = client.get('/_meta/metrics')
    assert m.status_code == 200
    body = m.json()
    assert body['ok'] is True
    metrics = body['metrics']
    assert 'calls' in metrics and 'errors' in metrics
    # At least one disabled error registered
    assert metrics['errors']['byCode'].get('disabled', 0) >= 1
