from __future__ import annotations

from typing import Any

import pytest

from mcpo.services import provider_models


class _CatalogResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _mock_catalog(monkeypatch, payload: dict[str, Any]) -> None:
    class _CatalogClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_CatalogClient":
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def get(
            self,
            url: str,
            *,
            headers: dict[str, str],
        ) -> _CatalogResponse:
            return _CatalogResponse(payload)

    monkeypatch.setattr(provider_models.httpx, "AsyncClient", _CatalogClient)


def _provider(kind: str) -> dict[str, Any]:
    return {
        "id": kind,
        "displayName": kind.title(),
        "kind": kind,
        "baseUrl": f"https://{kind}.example.test",
        "apiKey": "test-key",
        "billing": "paid",
        "capabilities": ["text", "images", "files", "tools", "reasoning"],
        "models": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "payload", "multimodal_id", "text_id"),
    [
        (
            "openai",
            {"data": [{"id": "gpt-5.6"}, {"id": "gpt-4"}]},
            "gpt-5.6",
            "gpt-4",
        ),
        (
            "anthropic",
            {
                "data": [
                    {"id": "claude-opus-4-8"},
                    {"id": "claude-internal-unknown"},
                ]
            },
            "claude-opus-4-8",
            "claude-internal-unknown",
        ),
        (
            "gemini",
            {
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-experimental-unknown",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            },
            "gemini-3.5-flash",
            "gemini-experimental-unknown",
        ),
    ],
)
async def test_direct_catalog_infers_only_known_multimodal_models(
    monkeypatch,
    kind: str,
    payload: dict[str, Any],
    multimodal_id: str,
    text_id: str,
) -> None:
    _mock_catalog(monkeypatch, payload)

    models = await provider_models.fetch_provider_models(_provider(kind))
    by_id = {model["id"]: model for model in models}

    multimodal = by_id[multimodal_id]
    assert multimodal["inputModalities"] == ["text", "image", "file"]
    assert {"images", "files"}.issubset(multimodal["capabilities"])

    text_only = by_id[text_id]
    assert text_only["inputModalities"] == ["text"]
    assert "images" not in text_only["capabilities"]
    assert "files" not in text_only["capabilities"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"inputModalities": ["text"]},
        {"architecture": {"input_modalities": ["text"]}},
    ],
)
def test_explicit_catalog_modalities_override_model_family_inference(
    metadata: dict[str, Any],
) -> None:
    model = provider_models.normalize_model(
        _provider("openai"),
        {
            "id": "gpt-5.6",
            **metadata,
        },
    )

    assert model is not None
    assert model["inputModalities"] == ["text"]
    assert "images" not in model["capabilities"]
    assert "files" not in model["capabilities"]
