import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_minimal_app():
    # Import inside to avoid side effects at module import time
    from mcpo.api.routers.meta import router as meta_router

    app = FastAPI()
    app.include_router(meta_router, prefix="/_meta")

    # Minimal state expected by meta endpoints
    app.state.bound_host = "127.0.0.1"
    app.state.bound_port = 8000
    app.state.mcp_proxy_url = None

    return app


def test_meta_logs_endpoints_basic():
    app = make_minimal_app()
    client = TestClient(app)

    # /_meta/logs should return ok and a logs array
    r = client.get("/_meta/logs")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert isinstance(body.get("logs"), list)

    # /_meta/logs/sources should return ok and include openapi source
    r2 = client.get("/_meta/logs/sources")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("ok") is True
    sources = body2.get("sources", [])
    assert any(s.get("id") == "openapi" for s in sources)
