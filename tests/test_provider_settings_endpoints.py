"""Focused provider-management and chat-settings endpoint contracts.

Every persistence path is created under pytest's temporary directory. Provider
connection checks are patched at the router boundary, so this module never
contacts a real inference service.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers.chat import router as chat_router
from mcpo.api.routers.providers import router as providers_router
from mcpo.services.provider_models import (
    test_provider_connection as run_provider_connection_test,
)
from mcpo.services.provider_registry import ProviderRegistry
from mcpo.services.state import StateManager


@pytest.fixture()
def provider_harness(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, FastAPI, ProviderRegistry, Path]]:
    registry_path = tmp_path / "providers.json"
    registry = ProviderRegistry(registry_path)
    app = FastAPI()
    app.include_router(providers_router, prefix="/chat")
    app.state.provider_registry = registry
    app.state.read_only_mode = False
    with TestClient(app) as client:
        yield client, app, registry, registry_path


def _seed_remote_provider(
    registry: ProviderRegistry,
    *,
    provider_id: str = "test-provider",
    api_key: str = "saved-provider-secret",
) -> None:
    registry.upsert_provider(
        provider_id=provider_id,
        display_name="Test Provider",
        kind="openai_compatible",
        base_url="https://provider.example/v1",
        api_key=api_key,
        billing="paid",
        capabilities=["text", "tools"],
        models=[{"id": "old-model", "label": "Old model"}],
    )


def _assert_secret_absent(payload: object, secret: str) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    assert secret not in serialized

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert "apiKey" not in value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)


def _live_probe_result(models: list[dict[str, object]]) -> dict[str, object]:
    return {
        "models": models,
        "verification_mode": "live",
        "status": "connected",
        "verified": True,
    }


def test_provider_crud_never_returns_saved_api_key(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, _app, registry, _registry_path = provider_harness
    secret = "provider-secret-that-must-not-leak"

    created = client.put(
        "/chat/providers/acme",
        json={
            "displayName": "Acme",
            "kind": "openai_compatible",
            "baseUrl": "https://acme.example/v1",
            "apiKey": secret,
            "billing": "paid",
            "capabilities": ["text", "tools"],
            "models": [{"id": "acme-chat", "label": "Acme Chat"}],
        },
    )
    assert created.status_code == 200, created.text
    _assert_secret_absent(created.json(), secret)
    assert created.json()["provider"]["hasApiKey"] is True

    updated = client.put(
        "/chat/providers/acme",
        json={"displayName": "Acme renamed", "enabled": False},
    )
    assert updated.status_code == 200, updated.text
    _assert_secret_absent(updated.json(), secret)
    assert registry.resolved_provider("acme")["apiKey"] == secret

    listed = client.get("/chat/providers")
    assert listed.status_code == 200, listed.text
    _assert_secret_absent(listed.json(), secret)
    public_acme = next(
        item for item in listed.json()["providers"] if item["id"] == "acme"
    )
    assert public_acme["hasApiKey"] is True
    assert public_acme["enabled"] is False

    removed = client.delete("/chat/providers/acme")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "removed": True}
    assert registry.get_provider("acme") is None


def test_provider_crud_and_probe_are_blocked_in_read_only_mode(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, app, registry, _registry_path = provider_harness
    _seed_remote_provider(registry)
    before = registry.get_provider("test-provider")
    app.state.read_only_mode = True

    with patch(
        "mcpo.api.routers.providers.test_provider_connection",
        new=AsyncMock(return_value=[]),
    ) as probe:
        responses = [
            client.put(
                "/chat/providers/test-provider",
                json={"enabled": False},
            ),
            client.delete("/chat/providers/test-provider"),
            client.post("/chat/providers/test-provider/test"),
        ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert all(response.json()["detail"] == "Read-only mode enabled" for response in responses)
    assert registry.get_provider("test-provider") == before
    probe.assert_not_awaited()

    listed = client.get("/chat/providers")
    assert listed.status_code == 200


def test_loopback_provider_can_be_saved_as_keyless_local_configuration(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, _app, registry, _registry_path = provider_harness

    response = client.put(
        "/chat/providers/local-lab",
        json={
            "displayName": "Local Lab",
            "kind": "openai_compatible",
            "baseUrl": "http://127.0.0.1:11434/v1",
            "billing": "local",
            "models": [{"id": "local-model", "label": "Local model"}],
        },
    )

    assert response.status_code == 200, response.text
    provider = response.json()["provider"]
    assert provider["configured"] is True
    assert provider["supportsKeyless"] is True
    assert provider["hasApiKey"] is False
    assert registry.resolved_provider("local-lab")["apiKey"] is None


def test_provider_probe_success_is_mocked_and_persists_verification(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, _app, registry, registry_path = provider_harness
    secret = "saved-provider-secret"
    _seed_remote_provider(registry, api_key=secret)
    discovered = [
        {
            "id": "verified-model",
            "key": "test-provider:verified-model",
            "label": "Verified model",
            "provider": "test-provider",
            "billing": "paid",
            "free": False,
            "capabilities": ["text", "tools"],
            "inputModalities": ["text"],
        }
    ]

    with patch(
        "mcpo.api.routers.providers.test_provider_connection",
        new=AsyncMock(return_value=_live_probe_result(discovered)),
    ) as probe:
        response = client.post("/chat/providers/test-provider/test")

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["verificationMode"] == "live"
    assert response.json()["status"] == "connected"
    assert response.json()["verified"] is True
    assert response.json()["modelCount"] == 1
    assert response.json()["models"] == discovered
    _assert_secret_absent(response.json(), secret)
    probe.assert_awaited_once_with("test-provider", registry)

    reloaded = ProviderRegistry(registry_path).resolved_provider("test-provider")
    assert reloaded is not None
    assert reloaded["lastVerifiedAt"] == response.json()["verifiedAt"]
    assert [model["id"] for model in reloaded["models"]] == ["verified-model"]
    assert reloaded["apiKey"] == secret


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_id,kind", [
    ("minimax", "minimax"),
    ("glm", "glm"),
    ("kimi", "kimi"),
])
async def test_static_catalog_probe_is_not_live_verification_and_makes_no_request(
    tmp_path: Path,
    provider_id: str,
    kind: str,
) -> None:
    registry = ProviderRegistry(tmp_path / f"{provider_id}.json")
    registry.upsert_provider(
        provider_id=provider_id,
        display_name=provider_id.upper(),
        kind=kind,
        base_url="https://provider.example/v1",
        api_key="not-used-by-static-catalog",
        models=[],
    )

    with patch("mcpo.services.provider_models.httpx.AsyncClient") as client_factory:
        result = await run_provider_connection_test(provider_id, registry)

    client_factory.assert_not_called()
    assert result["verification_mode"] == "catalog_only"
    assert result["status"] == "not_verified"
    assert result["verified"] is False
    assert isinstance(result["models"], list)


def test_catalog_only_probe_persists_models_without_false_verified_timestamp(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, _app, registry, registry_path = provider_harness
    _seed_remote_provider(registry)
    catalog_models = [{
        "id": "catalog-model",
        "key": "test-provider:catalog-model",
        "label": "Catalog model",
        "provider": "test-provider",
        "billing": "paid",
        "free": False,
        "capabilities": ["text"],
        "inputModalities": ["text"],
    }]
    catalog_result = {
        "models": catalog_models,
        "verification_mode": "catalog_only",
        "status": "not_verified",
        "verified": False,
    }

    with patch(
        "mcpo.api.routers.providers.test_provider_connection",
        new=AsyncMock(return_value=catalog_result),
    ):
        response = client.post("/chat/providers/test-provider/test")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verificationMode"] == "catalog_only"
    assert body["status"] == "not_verified"
    assert body["verified"] is False
    assert body["message"] == "Configuration saved; live connection not verified."
    assert "verifiedAt" not in body
    reloaded = ProviderRegistry(registry_path).resolved_provider("test-provider")
    assert reloaded is not None
    assert reloaded["lastVerifiedAt"] is None
    assert [model["id"] for model in reloaded["models"]] == ["catalog-model"]


def test_provider_probe_failure_is_502_without_false_verification(
    provider_harness: tuple[TestClient, FastAPI, ProviderRegistry, Path],
) -> None:
    client, _app, registry, registry_path = provider_harness
    secret = "saved-provider-secret"
    _seed_remote_provider(registry, api_key=secret)
    before = registry_path.read_bytes()
    failure = httpx.ConnectError(
        "dial failed",
        request=httpx.Request("GET", "https://provider.example/v1/models"),
    )

    with patch(
        "mcpo.api.routers.providers.test_provider_connection",
        new=AsyncMock(side_effect=failure),
    ) as probe:
        response = client.post("/chat/providers/test-provider/test")

    assert response.status_code == 502, response.text
    assert response.json() == {
        "detail": "Provider connection failed: ConnectError."
    }
    _assert_secret_absent(response.json(), secret)
    probe.assert_awaited_once_with("test-provider", registry)
    assert registry_path.read_bytes() == before
    assert registry.resolved_provider("test-provider")["lastVerifiedAt"] is None


def _chat_settings_app(
    state: StateManager,
    *,
    read_only: bool = False,
) -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router, prefix="/chat")
    app.state.state_manager = state
    app.state.read_only_mode = read_only
    return app


def test_chat_settings_camel_case_round_trip_persists_across_reload(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    state = StateManager(str(state_path))
    settings = {
        "defaultSystemPrompt": "Use tools, verify results, and stay concise.",
        "temperature": 0.25,
        "maxOutputTokens": 16384,
        "maxToolRounds": 12,
        "includeReasoning": False,
        "reasoningEffort": "high",
        "includeManagementTools": True,
    }

    with TestClient(_chat_settings_app(state)) as client:
        defaults = client.get("/chat/sessions/settings")
        assert defaults.status_code == 200
        assert defaults.json()["settings"]["defaultSystemPrompt"]

        saved = client.put("/chat/sessions/settings", json=settings)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"ok": True, "settings": settings}

    reloaded = StateManager(str(state_path))
    assert reloaded.get_chat_settings() == settings
    with TestClient(_chat_settings_app(reloaded)) as client:
        fetched = client.get("/chat/sessions/settings")
    assert fetched.status_code == 200
    assert fetched.json() == {"ok": True, "settings": settings}


def test_chat_settings_preserve_snake_case_clients_and_reject_unknown_fields(
    tmp_path: Path,
) -> None:
    state = StateManager(str(tmp_path / "state.json"))
    snake_case_settings = {
        "default_system_prompt": "Snake case client prompt",
        "temperature": 0.5,
        "max_output_tokens": 4096,
        "max_tool_rounds": 4,
        "include_reasoning": True,
        "reasoning_effort": "low",
        "include_management_tools": False,
    }
    expected = {
        "defaultSystemPrompt": "Snake case client prompt",
        "temperature": 0.5,
        "maxOutputTokens": 4096,
        "maxToolRounds": 4,
        "includeReasoning": True,
        "reasoningEffort": "low",
        "includeManagementTools": False,
    }

    with TestClient(_chat_settings_app(state)) as client:
        saved = client.put("/chat/sessions/settings", json=snake_case_settings)
        unknown = client.put(
            "/chat/sessions/settings",
            json={**snake_case_settings, "unexpectedSetting": True},
        )

    assert saved.status_code == 200, saved.text
    assert saved.json() == {"ok": True, "settings": expected}
    assert unknown.status_code == 422, unknown.text
    assert state.get_chat_settings() == expected


def test_chat_settings_mutation_is_blocked_in_read_only_mode(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    state = StateManager(str(state_path))
    original = {"defaultSystemPrompt": "Existing prompt"}
    state.set_chat_settings(original)
    before = state_path.read_bytes()

    with TestClient(_chat_settings_app(state, read_only=True)) as client:
        response = client.put(
            "/chat/sessions/settings",
            json={"defaultSystemPrompt": "Blocked replacement"},
        )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Read-only mode enabled"
    assert state.get_chat_settings() == original
    assert state_path.read_bytes() == before
