from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import completions, model_api_keys
from mcpo.api.routers.model_api_keys import router as model_api_keys_router
from mcpo.services.model_api_keys import ModelAPIKeyStore
from mcpo.services.usage import UsageRecorder, get_usage_recorder
from mcpo.utils.auth import ModelAPIKeyMiddleware


ADMIN_KEY = "admin-secret"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}"}


def test_recorder_counts_requests_errors_and_paths():
    rec = UsageRecorder()
    rec.record("abc", path="/v1/models", status_code=200)
    rec.record("abc", path="/v1/models", status_code=200)
    rec.record("abc", path="/v1/chat/completions", status_code=502)
    rec.record("abc", path="/v1/chat/completions", status_code=429)
    snap = rec.snapshot("abc")
    assert snap["requests"] == 4
    assert snap["errors"] == 2  # 502 + 429
    assert snap["rateLimited"] == 1
    assert snap["byPath"] == {"/v1/models": 2, "/v1/chat/completions": 2}
    assert snap["lastStatus"] == 429
    assert snap["sinceStartup"] is True
    # Unknown key gives zeros, not KeyError.
    assert rec.snapshot("nope")["requests"] == 0
    # Full snapshot keyed by id.
    assert set(rec.snapshot().keys()) == {"abc"}


def _build_app(store: ModelAPIKeyStore) -> FastAPI:
    app = FastAPI()
    app.state.api_key = ADMIN_KEY
    app.state.model_api_key_store = store
    app.state.read_only_mode = False
    app.include_router(model_api_keys_router, prefix="/chat")
    app.include_router(completions.router)
    app.add_middleware(ModelAPIKeyMiddleware, api_key=ADMIN_KEY)
    return app


def test_middleware_records_model_key_usage_not_admin(tmp_path, monkeypatch):
    get_usage_recorder().reset()
    store = ModelAPIKeyStore(tmp_path / "keys.json")
    token, record = store.create_key(name="Counted", scopes=["models:read"])

    async def fake_list_models():
        return [{"id": "gpt-codex", "key": "codex-oauth:gpt-codex", "provider": "codex-oauth"}]

    monkeypatch.setattr(completions, "_list_models", fake_list_models)
    monkeypatch.setattr(
        model_api_keys,
        "codex_oauth_status",
        lambda: {"ready": True, "source": "test"},
    )
    client = _build_app(store)
    client = TestClient(client)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/models", headers=headers).status_code == 200
    assert client.get("/v1/models", headers=headers).status_code == 200
    # Admin requests are not counted.
    assert client.get("/v1/models", headers=ADMIN_HEADERS).status_code == 200

    snap = get_usage_recorder().snapshot(record["id"])
    assert snap["requests"] == 2
    assert snap["errors"] == 0
    assert snap["byPath"] == {"/v1/models": 2}

    # Admin key list surfaces the usage per key.
    listed = client.get("/chat/providers/codex-oauth/access-keys", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    body = listed.json()
    assert body["usageScope"] == "process"
    assert body["keys"][0]["usage"]["requests"] == 2

    # whoami surfaces the caller's own usage (this call itself increments).
    who = client.get("/v1/whoami", headers=headers)
    assert who.status_code == 200
    assert who.json()["usage"]["requests"] >= 2

    get_usage_recorder().reset()
