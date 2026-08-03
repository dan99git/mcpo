"""Security and compatibility regressions for the completion router."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import completions


_PROVIDER_ENV_KEYS = (
    "OPEN_AI_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MINIMAX_API_KEY",
)
_PROVIDER_BASE_URL_ENV_KEYS = (
    "OPENAI_BASE_URL",
    "OPEN_AI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "GEMINI_BASE_URL",
    "GOOGLE_BASE_URL",
    "MINIMAX_BASE_URL",
)


def _clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_PROVIDER_ENV_KEYS, *_PROVIDER_BASE_URL_ENV_KEYS):
        monkeypatch.delenv(name, raising=False)


def _request(
    provider: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    stream: bool = False,
) -> completions.CompletionRequest:
    return completions.CompletionRequest(
        model="test-model",
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        stream=stream,
        messages=[{"role": "user", "content": "test"}],
    )


@pytest.mark.parametrize(
    ("provider_id", "environment_key"),
    [
        ("openai", "OPEN_AI_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("minimax", "MINIMAX_API_KEY"),
    ],
)
def test_remote_request_base_url_never_inherits_server_key(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    environment_key: str,
) -> None:
    _clear_provider_environment(monkeypatch)
    server_secret = f"server-held-{provider_id}-secret"
    monkeypatch.setenv(environment_key, server_secret)

    with pytest.raises(
        completions.CompletionProviderError,
        match="request-supplied api_key",
    ) as exc_info:
        completions._resolve_provider(
            _request(provider_id, base_url="https://attacker.example/v1")
        )

    assert server_secret not in str(exc_info.value)


def test_remote_request_base_url_uses_only_request_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "server-held-secret")

    provider = completions._resolve_provider(
        _request(
            "openai",
            base_url="https://gateway.example/v1/",
            api_key="request-secret",
        )
    )

    assert isinstance(provider, completions.OpenAICompatibleProvider)
    assert provider.base_url == "https://gateway.example/v1"
    assert provider.api_key == "request-secret"


def test_openai_compatible_loopback_can_be_keyless_without_inheriting_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("OPEN_AI_API_KEY", "server-held-secret")

    provider = completions._resolve_provider(
        _request("openai", base_url="http://127.0.0.1:11434/v1")
    )

    assert isinstance(provider, completions.OpenAICompatibleProvider)
    assert provider.base_url == "http://127.0.0.1:11434/v1"
    assert provider.api_key is None
    assert "Authorization" not in provider._headers()


def test_anthropic_loopback_still_requires_request_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-held-secret")

    with pytest.raises(
        completions.CompletionProviderError,
        match="request-supplied api_key",
    ):
        completions._resolve_provider(
            _request("anthropic", base_url="http://localhost:8080")
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://gateway.example/v1",
        "https://user:password@gateway.example/v1",
        "https://gateway.example/v1?token=secret",
        "https://gateway.example/v1#fragment",
    ],
)
def test_request_base_url_uses_registry_validation(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    _clear_provider_environment(monkeypatch)

    with pytest.raises(completions.CompletionProviderError):
        completions._resolve_provider(
            _request("openai", base_url=base_url, api_key="request-secret")
        )


def test_unknown_provider_returns_controlled_client_error() -> None:
    app = FastAPI()
    app.include_router(completions.router)

    with patch(
        "mcpo.api.routers.completions.compile_skills_system_prompt",
        return_value="",
    ), TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "provider": "not-a-provider",
                "messages": [{"role": "user", "content": "test"}],
            },
        )

    assert response.status_code in {400, 422}, response.text
    assert "provider" in response.text.lower()
    assert "not-a-provider" in response.text.lower()


def test_minimax_stream_delegates_async_iterator_without_awaiting_it() -> None:
    payload = _request("minimax", stream=True)
    provider = completions.MiniMaxProvider(base_url=None, api_key="request-secret")

    async def fake_stream(self, request):
        assert request is payload
        yield 'data: {"choices":[]}\n\n'
        yield "data: [DONE]\n\n"

    async def collect_chunks() -> list[str]:
        return [chunk async for chunk in provider.stream(payload)]

    with patch.object(completions.AnthropicProvider, "stream", new=fake_stream):
        chunks = asyncio.run(collect_chunks())

    assert chunks == [
        'data: {"choices":[]}\n\n',
        "data: [DONE]\n\n",
    ]


def test_model_endpoint_delegates_to_shared_catalog_and_keeps_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_provider_environment(monkeypatch)
    models = [
        {
            "id": "shared-model",
            "key": "openai:shared-model",
            "label": "OpenAI shared",
            "provider": "openai",
        },
        {
            "id": "shared-model",
            "key": "openrouter:shared-model",
            "label": "OpenRouter shared",
            "provider": "openrouter",
        },
    ]
    state = MagicMock()
    state.get_favorite_models.return_value = []
    app = FastAPI()
    app.include_router(completions.router)

    with patch(
        "mcpo.services.model_catalog.list_all_models",
        new=AsyncMock(return_value=models),
    ) as list_all_models, patch(
        "mcpo.services.state.get_state_manager",
        return_value=state,
    ), TestClient(app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {"id": "openai:shared-model", "object": "model"},
            {"id": "openrouter:shared-model", "object": "model"},
        ],
        "models": models,
    }
    list_all_models.assert_awaited_once_with()


def test_favorites_prefer_provider_qualified_model_keys() -> None:
    models = [
        {
            "id": "shared-model",
            "key": "openai:shared-model",
            "provider": "openai",
        },
        {
            "id": "shared-model",
            "key": "openrouter:shared-model",
            "provider": "openrouter",
        },
    ]
    state = MagicMock()
    state.get_favorite_models.return_value = ["openrouter:shared-model"]

    with patch("mcpo.services.state.get_state_manager", return_value=state):
        filtered = completions._filter_by_favorites(models)

    assert filtered == [models[1]]


def test_legacy_bare_favorite_id_matches_all_provider_duplicates() -> None:
    models = [
        {
            "id": "shared-model",
            "key": "openai:shared-model",
            "provider": "openai",
        },
        {
            "id": "shared-model",
            "key": "openrouter:shared-model",
            "provider": "openrouter",
        },
    ]
    state = MagicMock()
    state.get_favorite_models.return_value = ["shared-model"]

    with patch("mcpo.services.state.get_state_manager", return_value=state):
        filtered = completions._filter_by_favorites(models)

    assert filtered == models
