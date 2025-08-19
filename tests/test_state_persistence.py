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
