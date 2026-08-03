"""Completion routing contracts for providers saved through the web registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import completions
from mcpo.services import provider_models
from mcpo.services.provider_registry import ProviderRegistry, get_provider_registry


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(completions.router)
    return app


def _save_provider(
    registry: ProviderRegistry,
    *,
    provider_id: str,
    api_key: str,
    model_id: str = "shared-model",
) -> None:
    registry.upsert_provider(
        provider_id=provider_id,
        display_name=provider_id.title(),
        kind="openai_compatible",
        base_url=f"https://{provider_id}.example.test/v1",
        api_key=api_key,
        models=[{"id": model_id, "label": f"{provider_id} {model_id}"}],
    )


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProviderRegistry:
    registry_path = tmp_path / "providers.json"
    monkeypatch.setenv("MCPO_PROVIDER_CONFIG_PATH", str(registry_path))
    for name in (
        "OPEN_AI_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "MINIMAX_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    async def saved_models_only(provider: Dict[str, Any]):
        return [
            provider_models.normalize_model(provider, model)
            for model in provider.get("models", [])
        ]

    monkeypatch.setattr(provider_models, "fetch_provider_models", saved_models_only)
    monkeypatch.setattr(completions, "compile_skills_system_prompt", lambda **_: "")
    return get_provider_registry(registry_path)


def _capture_completion(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    captured: Dict[str, Any] = {}

    async def fake_complete(
        self: completions.OpenAICompatibleProvider,
        payload: completions.CompletionRequest,
    ) -> Dict[str, Any]:
        captured.update(
            provider=self.name,
            base_url=self.base_url,
            api_key=self.api_key,
            model=payload.model,
        )
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": payload.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setattr(completions.OpenAICompatibleProvider, "complete", fake_complete)
    return captured


def _post(client: TestClient, model: str, **overrides: Any):
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "test"}],
    }
    payload.update(overrides)
    return client.post("/v1/chat/completions", json=payload)


def test_saved_custom_provider_routes_unambiguous_bare_model_without_key_leak(
    registry: ProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "acme-saved-secret"
    _save_provider(registry, provider_id="acme", api_key=secret, model_id="acme-chat")
    captured = _capture_completion(monkeypatch)

    with TestClient(_app()) as client:
        response = _post(client, "acme-chat")

    assert response.status_code == 200, response.text
    assert captured == {
        "provider": "acme",
        "base_url": "https://acme.example.test/v1",
        "api_key": secret,
        "model": "acme-chat",
    }
    assert secret not in response.text


def test_provider_qualified_model_from_models_endpoint_round_trips(
    registry: ProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(registry, provider_id="acme", api_key="acme-secret")
    _save_provider(registry, provider_id="beta", api_key="beta-secret")
    captured = _capture_completion(monkeypatch)

    with TestClient(_app()) as client:
        models_response = client.get("/v1/models")
        assert models_response.status_code == 200, models_response.text
        model_ids = [item["id"] for item in models_response.json()["data"]]
        assert model_ids == ["acme:shared-model", "beta:shared-model"]

        response = _post(client, "beta:shared-model")

    assert response.status_code == 200, response.text
    assert captured == {
        "provider": "beta",
        "base_url": "https://beta.example.test/v1",
        "api_key": "beta-secret",
        "model": "shared-model",
    }
    serialized = json.dumps(response.json(), sort_keys=True)
    assert "acme-secret" not in serialized
    assert "beta-secret" not in serialized


def test_ambiguous_bare_model_requires_provider_qualified_id(
    registry: ProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_provider(registry, provider_id="acme", api_key="acme-secret")
    _save_provider(registry, provider_id="beta", api_key="beta-secret")
    captured = _capture_completion(monkeypatch)

    with TestClient(_app()) as client:
        response = _post(client, "shared-model")

    assert response.status_code == 400
    assert captured == {}
    detail = response.json()["detail"]
    assert "ambiguous" in detail.lower()
    assert "acme:shared-model" in detail
    assert "beta:shared-model" in detail
    assert "acme-secret" not in response.text
    assert "beta-secret" not in response.text


def test_saved_key_is_not_reused_for_request_supplied_custom_host(
    registry: ProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "acme-saved-secret"
    _save_provider(registry, provider_id="acme", api_key=secret)
    captured = _capture_completion(monkeypatch)

    with TestClient(_app()) as client:
        response = _post(
            client,
            "acme:shared-model",
            base_url="https://other.example.test/v1",
        )

    assert response.status_code == 400
    assert captured == {}
    assert "request-supplied api_key" in response.json()["detail"]
    assert secret not in response.text
