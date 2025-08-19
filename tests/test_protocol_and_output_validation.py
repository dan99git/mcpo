import json
import asyncio
import pytest
from fastapi.testclient import TestClient

@pytest.mark.asyncio
async def test_protocol_version_warn_allows_call(tmp_path, monkeypatch):
    from mcpo.main import build_main_app
    from fastapi import FastAPI
    from mcpo.utils.main import get_tool_handler

    class FakeSession:
        async def call_tool(self, name, arguments):
            return type('R', (), {'isError': False, 'content': []})

    app = await build_main_app(protocol_version_mode='warn')
    sub = FastAPI(title='sP')
    sub.state.parent_app = app
    sub.state.session = FakeSession()
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None)
    sub.post('/demo')(handler)
    app.mount('/sP', sub)
    app.state.server_enabled['sP'] = True
    app.state.tool_enabled.setdefault('sP', {})['demo'] = True

    client = TestClient(app)
    # No MCP-Protocol-Version header but warn mode, should succeed
    r = client.post('/sP/demo')
    assert r.status_code == 200, r.text

@pytest.mark.asyncio
async def test_protocol_version_enforce_blocks_missing_header(tmp_path):
    from mcpo.main import build_main_app
    from fastapi import FastAPI
    from mcpo.utils.main import get_tool_handler

    class FakeSession:
        async def call_tool(self, name, arguments):
            return type('R', (), {'isError': False, 'content': []})

    app = await build_main_app(protocol_version_mode='enforce')
    sub = FastAPI(title='sE')
    sub.state.parent_app = app
    sub.state.session = FakeSession()
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None)
    sub.post('/demo')(handler)
    app.mount('/sE', sub)
    app.state.server_enabled['sE'] = True
    app.state.tool_enabled.setdefault('sE', {})['demo'] = True

    client = TestClient(app)
    r = client.post('/sE/demo')
    assert r.status_code == 426
    js = r.json()
    assert js['ok'] is False and js['error']['code'] == 'protocol'

@pytest.mark.asyncio
async def test_output_validation_enforce_blocks_invalid(tmp_path):
    from mcpo.main import build_main_app
    from fastapi import FastAPI
    from mcpo.utils.main import get_tool_handler, create_model
    from pydantic import BaseModel

    # Fake session returning invalid (string) where model expects object
    class FakeSession:
        async def call_tool(self, name, arguments):
            # Simulate output content list -> will process into [] => success envelope expects list
            return type('R', (), {'isError': False, 'content': []})

    # Build app with enforce validation
    app = await build_main_app(validate_output_mode='enforce')
    sub = FastAPI(title='sV')
    sub.state.parent_app = app
    sub.state.session = FakeSession()

    # Provide a synthetic response model expecting a field 'value'
    response_model_fields = {'value': (int, ...)}
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None, response_model_fields=response_model_fields)
    sub.post('/demo')(handler)
    app.mount('/sV', sub)
    app.state.server_enabled['sV'] = True
    app.state.tool_enabled.setdefault('sV', {})['demo'] = True

    client = TestClient(app)
    # Our handler will produce final_response = [] which violates schema expecting object with value:int -> triggers validation error
    r = client.post('/sV/demo', headers={'MCP-Protocol-Version': app.state.supported_protocol_versions[0]})
    assert r.status_code == 502
    js = r.json()
    assert js['ok'] is False and js['error']['code'] == 'output_validation'

@pytest.mark.asyncio
async def test_latency_metrics_accumulate(tmp_path):
    from mcpo.main import build_main_app
    from fastapi import FastAPI
    from mcpo.utils.main import get_tool_handler

    class FakeSession:
        async def call_tool(self, name, arguments):
            await asyncio.sleep(0.01)
            return type('R', (), {'isError': False, 'content': []})

    app = await build_main_app()
    sub = FastAPI(title='sL')
    sub.state.parent_app = app
    sub.state.session = FakeSession()
    handler = get_tool_handler(sub.state.session, 'demo', form_model_fields=None)
    sub.post('/demo')(handler)
    app.mount('/sL', sub)
    app.state.server_enabled['sL'] = True
    app.state.tool_enabled.setdefault('sL', {})['demo'] = True

    client = TestClient(app)
    for _ in range(3):
        r = client.post('/sL/demo', headers={'MCP-Protocol-Version': app.state.supported_protocol_versions[0]})
        assert r.status_code == 200
    m = client.get('/_meta/metrics')
    js = m.json()
    assert js['ok'] is True
    per_tool = js['metrics']['perTool']
    assert 'demo' in per_tool
    assert per_tool['demo']['calls'] >= 3
    # avgLatencyMs should be > 0
    assert per_tool['demo']['avgLatencyMs'] > 0
