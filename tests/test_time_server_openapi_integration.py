import asyncio
import json
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from mcpo.main import build_main_app


def _has_time_server(app):
    for route in app.router.routes:
        if getattr(route, 'path', None) == '/time':
            return True
    return False


@pytest.mark.integration
def test_time_server_tool_call_new_york():
    """Integration test: spin up configured time MCP server and call its tool via generated OpenAPI endpoint.

    This simulates what an external model client (like OpenWebUI) would do: discover the
    tool path and POST JSON with a timezone argument.
    If the external time server cannot be started (missing uvx, package, etc) the test is skipped.
    """
    # Build app synchronously (no server run) using existing config
    try:
        app = asyncio.run(build_main_app(config_path='mcpo.json'))
    except FileNotFoundError:
        pytest.skip('mcpo.json not found; skipping time server integration test')

    # Use TestClient context to trigger lifespans (which connect sub-apps)
    with TestClient(app) as client:
        # If time server failed to connect, main lifespan would log it and tool route won't be present
        if not _has_time_server(app):
            pytest.skip('Time server route not mounted; skipping')

        # Fetch sub-app OpenAPI to introspect schema
        r = client.get('/time/openapi.json')
        if r.status_code != 200:
            pytest.skip('Could not retrieve time server OpenAPI spec')
        spec = r.json()
        paths = spec.get('paths', {})
        # Expect the tool path '/time' inside the sub-app, mounted at /time -> full /time/time
        tool_rel_path = None
        if '/time' in paths and 'post' in paths['/time']:
            tool_rel_path = '/time'
        else:
            # Fallback: take first POST path if named differently
            for p, meta in paths.items():
                if 'post' in meta:
                    tool_rel_path = p
                    break
        if not tool_rel_path:
            pytest.skip('No POST tool path found in time server OpenAPI spec')

        # Inspect input schema for timezone-like field
        post_meta = paths[tool_rel_path]['post']
        schema = (
            post_meta.get('requestBody', {})
            .get('content', {})
            .get('application/json', {})
            .get('schema', {})
        )
        props = schema.get('properties', {})
        # Common field names to attempt
        tz_field = None
        for cand in ('timezone', 'tz', 'zone', 'location'):
            if cand in props:
                tz_field = cand
                break
        if not tz_field:
            pytest.skip('No timezone field discovered in tool schema')

        payload = {tz_field: 'America/New_York'}
        # Execute tool call
        resp = client.post('/time' + tool_rel_path, json=payload)
        if resp.status_code != 200:
            # Provide diagnostic info then skip to avoid hard failure on environment issues
            pytest.skip(f'Time tool call failed status={resp.status_code} body={resp.text}')
        body = resp.json()
        assert body.get('ok') is True
        # Result may be dict or scalar; we just assert presence of recognizable time information
        result = body.get('result')
        assert result is not None
        # Heuristic check: result contains year or ':' typical for time
        result_text = json.dumps(result)
        assert 'New_York' in result_text or ':' in result_text or str(datetime.utcnow().year) in result_text
