from fastapi import FastAPI
from fastapi.testclient import TestClient

def test_health_endpoint_basic():
    from mcpo.main import _register_health_endpoint  # internal helper
    app = FastAPI()
    _register_health_endpoint(app)
    client = TestClient(app)
    resp = client.get('/healthz')
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'ok'
    assert 'generation' in body
