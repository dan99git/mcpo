from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import completions
from mcpo.api.routers.model_api_keys import router as model_api_keys_router
from mcpo.services.model_api_keys import ModelAPIKeyStore
from mcpo.services.rate_limit import SlidingWindowRateLimiter, reset_rate_limiter
from mcpo.utils.auth import ModelAPIKeyMiddleware


ADMIN_KEY = "admin-secret"


def test_limiter_disabled_when_limit_zero():
    limiter = SlidingWindowRateLimiter(0)
    assert limiter.enabled is False
    for _ in range(1000):
        allowed, remaining, retry = limiter.check("k")
        assert allowed is True and remaining == -1 and retry == 0.0


def test_limiter_sliding_window_allows_then_blocks_then_recovers():
    limiter = SlidingWindowRateLimiter(2)
    now = 100.0
    assert limiter.check("k", now=now)[0] is True
    assert limiter.check("k", now=now)[0] is True
    blocked, remaining, retry = limiter.check("k", now=now)
    assert blocked is False and remaining == 0 and retry > 0
    # A different identity has its own budget.
    assert limiter.check("other", now=now)[0] is True
    # After the window slides past the first hit, capacity returns.
    assert limiter.check("k", now=now + 61)[0] is True


def _build_app(store: ModelAPIKeyStore) -> FastAPI:
    app = FastAPI()
    app.state.api_key = ADMIN_KEY
    app.state.model_api_key_store = store
    app.state.read_only_mode = False
    app.include_router(model_api_keys_router, prefix="/chat")
    app.include_router(completions.router)
    app.add_middleware(ModelAPIKeyMiddleware, api_key=ADMIN_KEY)
    return app


def test_rate_limit_returns_429_for_model_key_not_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("MCPO_MODEL_KEY_RPM", "1")
    reset_rate_limiter()
    try:
        store = ModelAPIKeyStore(tmp_path / "keys.json")
        token, _ = store.create_key(name="Limited", scopes=["models:read"])

        async def fake_list_models():
            return [{"id": "gpt-codex", "key": "codex-oauth:gpt-codex", "provider": "codex-oauth"}]

        monkeypatch.setattr(completions, "_list_models", fake_list_models)
        client = TestClient(_build_app(store))
        headers = {"Authorization": f"Bearer {token}"}

        first = client.get("/v1/models", headers=headers)
        assert first.status_code == 200
        second = client.get("/v1/models", headers=headers)
        assert second.status_code == 429
        assert second.headers.get("Retry-After") is not None
        assert second.headers.get("X-RateLimit-Limit") == "1"

        # Admin key is never rate limited.
        for _ in range(5):
            assert client.get(
                "/v1/models",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"},
            ).status_code == 200
    finally:
        reset_rate_limiter()


def test_success_carries_ratelimit_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("MCPO_MODEL_KEY_RPM", "5")
    reset_rate_limiter()
    try:
        store = ModelAPIKeyStore(tmp_path / "keys.json")
        token, _ = store.create_key(name="Budgeted", scopes=["models:read"])

        async def fake_list_models():
            return [{"id": "gpt-codex", "key": "codex-oauth:gpt-codex", "provider": "codex-oauth"}]

        monkeypatch.setattr(completions, "_list_models", fake_list_models)
        client = TestClient(_build_app(store))
        resp = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.headers.get("X-RateLimit-Limit") == "5"
        # First hit consumed one of five -> four remain.
        assert resp.headers.get("X-RateLimit-Remaining") == "4"
    finally:
        reset_rate_limiter()
