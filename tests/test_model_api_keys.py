from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import completions, model_api_keys
from mcpo.api.routers.model_api_keys import router as model_api_keys_router
from mcpo.services.model_api_keys import ModelAPIKeyStore
from mcpo.utils.auth import ModelAPIKeyMiddleware, get_verify_api_key


ADMIN_KEY = "admin-secret"
ADMIN_HEADERS = {"Authorization": f"Bearer {ADMIN_KEY}"}


def _future_timestamp() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _build_test_app(store: ModelAPIKeyStore) -> FastAPI:
    app = FastAPI(dependencies=[Depends(get_verify_api_key(ADMIN_KEY))])
    app.state.api_key = ADMIN_KEY
    app.state.model_api_key_store = store
    app.state.read_only_mode = False
    app.include_router(model_api_keys_router, prefix="/chat")
    app.include_router(completions.router)
    app.add_middleware(ModelAPIKeyMiddleware, api_key=ADMIN_KEY)
    return app


def test_store_hashes_secret_persists_metadata_and_revokes(tmp_path):
    path = tmp_path / "model-api-keys.json"
    store = ModelAPIKeyStore(path)

    token, record = store.create_key(
        name="OpenWebUI",
        scopes=["models:read", "responses:write"],
        expires_at=_future_timestamp(),
    )

    persisted = path.read_text(encoding="utf-8")
    assert token not in persisted
    assert token.split(".", 1)[1] not in persisted
    assert record["status"] == "active"
    assert store.enforcement_enabled() is True
    assert store.authenticate(token, required_scope="models:read") is not None

    reloaded = ModelAPIKeyStore(path)
    assert reloaded.enforcement_enabled() is True
    assert reloaded.authenticate(token, required_scope="responses:write") is not None
    assert reloaded.authenticate(token, required_scope="unsupported") is None

    revoked = reloaded.revoke_key(record["id"])
    assert revoked["status"] == "revoked"
    assert reloaded.authenticate(token, required_scope="models:read") is None
    assert ModelAPIKeyStore(path).enforcement_enabled() is True


def test_stale_store_cannot_resurrect_a_revoked_key(tmp_path):
    path = tmp_path / "model-api-keys.json"
    first = ModelAPIKeyStore(path)
    token, record = first.create_key(
        name="Shared workers",
        scopes=["models:read"],
    )
    stale = ModelAPIKeyStore(path)

    first.revoke_key(record["id"])

    assert stale.authenticate(token, required_scope="models:read") is None
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["keys"][0]["revokedAt"] is not None


def test_management_endpoints_require_admin_and_return_secret_once(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        model_api_keys,
        "codex_oauth_status",
        lambda: {"ready": True, "source": "test"},
    )
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    client = TestClient(_build_test_app(store))

    missing = client.get("/chat/providers/codex-oauth/access-keys")
    assert missing.status_code == 401

    created = client.post(
        "/chat/providers/codex-oauth/access-keys",
        headers=ADMIN_HEADERS,
        json={
            "name": "Desktop client",
            "scopes": ["models:read"],
            "expiresAt": _future_timestamp(),
        },
    )
    assert created.status_code == 201
    body = created.json()
    token = body["key"]
    record = body["record"]
    assert token.startswith(record["prefix"] + ".")
    assert "secretHash" not in record

    listed = client.get(
        "/chat/providers/codex-oauth/access-keys",
        headers=ADMIN_HEADERS,
    )
    assert listed.status_code == 200
    listed_body = listed.json()
    assert listed_body["enforced"] is True
    assert listed_body["keys"][0]["id"] == record["id"]
    assert "key" not in listed_body["keys"][0]
    assert "secretHash" not in listed_body["keys"][0]
    assert token not in json.dumps(listed_body)

    generated_key_admin_request = client.get(
        "/chat/providers/codex-oauth/access-keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert generated_key_admin_request.status_code == 403

    revoked = client.post(
        f"/chat/providers/codex-oauth/access-keys/{record['id']}/revoke",
        headers=ADMIN_HEADERS,
    )
    assert revoked.status_code == 200
    assert revoked.json()["record"]["status"] == "revoked"

    revoked_again = client.post(
        f"/chat/providers/codex-oauth/access-keys/{record['id']}/revoke",
        headers=ADMIN_HEADERS,
    )
    assert revoked_again.status_code == 200
    assert revoked_again.json()["record"]["revokedAt"] == revoked.json()["record"]["revokedAt"]


def test_management_requires_configured_admin_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MCPO_API_KEY", raising=False)
    app = FastAPI()
    app.state.api_key = None
    app.state.model_api_key_store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    app.state.read_only_mode = False
    app.include_router(model_api_keys_router, prefix="/chat")

    response = TestClient(app).get("/chat/providers/codex-oauth/access-keys")
    assert response.status_code == 503


def test_management_accepts_admin_key_loaded_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MCPO_API_KEY", ADMIN_KEY)
    app = FastAPI()
    app.state.api_key = None
    app.state.model_api_key_store = ModelAPIKeyStore(
        tmp_path / "model-api-keys.json"
    )
    app.state.read_only_mode = False
    app.include_router(model_api_keys_router, prefix="/chat")

    response = TestClient(app).get(
        "/chat/providers/codex-oauth/access-keys",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200


def test_admin_key_protects_model_routes_before_first_generated_key(
    tmp_path,
    monkeypatch,
):
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")

    async def fake_list_models():
        return []

    monkeypatch.setattr(completions, "_list_models", fake_list_models)
    client = TestClient(_build_test_app(store))

    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers=ADMIN_HEADERS).status_code == 200


def test_key_creation_requires_codex_cli_login(tmp_path, monkeypatch):
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    monkeypatch.setattr(
        model_api_keys,
        "codex_oauth_status",
        lambda: {
            "ready": False,
            "source": "codex-cli",
            "detail": "Run 'codex login'.",
        },
    )
    response = TestClient(_build_test_app(store)).post(
        "/chat/providers/codex-oauth/access-keys",
        headers=ADMIN_HEADERS,
        json={"name": "Blocked", "scopes": ["models:read"]},
    )
    assert response.status_code == 409
    assert store.list_keys() == []


def test_read_only_mode_blocks_key_mutations(tmp_path):
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    app = _build_test_app(store)
    app.state.read_only_mode = True
    response = TestClient(app).post(
        "/chat/providers/codex-oauth/access-keys",
        headers=ADMIN_HEADERS,
        json={"name": "Blocked", "scopes": ["models:read"]},
    )
    assert response.status_code == 403
    assert store.list_keys() == []


def test_generated_keys_enforce_scopes_and_filter_models(tmp_path, monkeypatch):
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    models_token, _ = store.create_key(
        name="Models only",
        scopes=["models:read"],
    )
    responses_token, responses_record = store.create_key(
        name="Responses only",
        scopes=["responses:write"],
    )

    async def fake_list_models():
        return [
            {
                "id": "gpt-codex",
                "key": "codex-oauth:gpt-codex",
                "provider": "codex-oauth",
            },
            {
                "id": "other-model",
                "key": "other:other-model",
                "provider": "other",
            },
        ]

    monkeypatch.setattr(completions, "_list_models", fake_list_models)
    client = TestClient(_build_test_app(store))

    assert client.get("/v1/models").status_code == 401
    assert client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {responses_token}"},
    ).status_code == 403

    models_response = client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {models_token}"},
    )
    assert models_response.status_code == 200
    assert [item["provider"] for item in models_response.json()["models"]] == [
        "codex-oauth"
    ]

    write_with_read_key = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {models_token}"},
        json={
            "model": "gpt-codex",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert write_with_read_key.status_code == 403

    provider_override = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {responses_token}"},
        json={
            "model": "other-model",
            "provider": "other",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert provider_override.status_code == 403
    assert "restricted to provider 'codex-oauth'" in provider_override.json()["detail"]

    connection_override = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {responses_token}"},
        json={
            "model": "gpt-codex",
            "base_url": "https://other.example/v1",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert connection_override.status_code == 403
    assert "cannot override provider connection settings" in connection_override.json()["detail"]

    store.revoke_key(responses_record["id"])
    revoked_response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {responses_token}"},
        json={
            "model": "gpt-codex",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert revoked_response.status_code == 403

    master_response = client.get("/v1/models", headers=ADMIN_HEADERS)
    assert master_response.status_code == 200
    assert {item["provider"] for item in master_response.json()["models"]} == {
        "codex-oauth",
        "other",
    }


def test_generated_key_auth_is_enforced_under_asgi_mount(tmp_path, monkeypatch):
    store = ModelAPIKeyStore(tmp_path / "model-api-keys.json")
    token, _ = store.create_key(name="Mounted client", scopes=["models:read"])

    async def fake_list_models():
        return [
            {
                "id": "gpt-codex",
                "key": "codex-oauth:gpt-codex",
                "provider": "codex-oauth",
            }
        ]

    monkeypatch.setattr(completions, "_list_models", fake_list_models)
    parent = FastAPI()
    parent.mount("/gateway", _build_test_app(store))
    client = TestClient(parent)

    assert client.get("/gateway/v1/models").status_code == 401
    response = client.get(
        "/gateway/v1/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["models"][0]["provider"] == "codex-oauth"
