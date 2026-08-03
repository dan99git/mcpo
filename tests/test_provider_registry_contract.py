"""Contract tests for the future secure provider registry service.

The registry is intentionally separate from MCP server configuration and the
process-wide environment.  Every test uses a temporary registry file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _new_registry(path: Path):
    from mcpo.services.provider_registry import ProviderRegistry

    return ProviderRegistry(path)


def _provider_error_type():
    from mcpo.services.provider_registry import ProviderRegistryError

    return ProviderRegistryError


def _upsert_test_provider(
    registry,
    *,
    provider_id: str = "openai",
    base_url: str = "https://api.example.test/v1",
    api_key: str | None = "test-secret",
    model_id: str = "test-model",
):
    return registry.upsert_provider(
        provider_id=provider_id,
        display_name=provider_id.title(),
        base_url=base_url,
        api_key=api_key,
        models=[{"id": model_id, "label": model_id}],
    )


def test_public_snapshot_never_serializes_saved_api_keys(tmp_path: Path) -> None:
    secret = "provider-secret-that-must-not-leak"
    registry = _new_registry(tmp_path / "providers.json")

    _upsert_test_provider(registry, api_key=secret)
    public = registry.public_snapshot()
    serialized = json.dumps(public, sort_keys=True)

    assert secret not in serialized
    assert "apiKey" not in public["providers"][0]
    assert public["providers"][0]["hasApiKey"] is True


def test_provider_qualified_model_keys_preserve_duplicate_upstream_ids(
    tmp_path: Path,
) -> None:
    registry = _new_registry(tmp_path / "providers.json")
    shared_model_id = "shared-model"

    _upsert_test_provider(
        registry,
        provider_id="openai",
        base_url="https://api.openai.example/v1",
        api_key="openai-secret",
        model_id=shared_model_id,
    )
    _upsert_test_provider(
        registry,
        provider_id="openrouter",
        base_url="https://openrouter.example/api/v1",
        api_key="openrouter-secret",
        model_id=shared_model_id,
    )

    providers = registry.public_snapshot()["providers"]
    models = [model for provider in providers for model in provider["models"]]

    assert [model["id"] for model in models].count(shared_model_id) == 2
    assert {model["key"] for model in models} == {
        "openai:shared-model",
        "openrouter:shared-model",
    }
    assert {model["provider"] for model in models} == {"openai", "openrouter"}


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com/v1",
        "http://10.0.0.25:8080/v1",
        "http://localhost.evil.example/v1",
    ],
)
def test_non_loopback_http_base_urls_are_rejected(
    tmp_path: Path, base_url: str
) -> None:
    registry = _new_registry(tmp_path / "providers.json")

    with pytest.raises(_provider_error_type(), match="loopback|https"):
        _upsert_test_provider(registry, base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
    ],
)
def test_base_urls_with_credentials_query_or_fragment_are_rejected(
    tmp_path: Path, base_url: str
) -> None:
    registry = _new_registry(tmp_path / "providers.json")

    with pytest.raises(_provider_error_type(), match="credentials|query|fragment"):
        _upsert_test_provider(registry, base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_loopback_http_provider_can_be_keyless(
    tmp_path: Path, base_url: str
) -> None:
    registry = _new_registry(tmp_path / "providers.json")

    _upsert_test_provider(
        registry,
        provider_id="local",
        base_url=base_url,
        api_key=None,
    )
    provider = registry.public_snapshot()["providers"][0]

    assert provider["baseUrl"] == base_url
    assert provider["hasApiKey"] is False


def test_failed_reload_preserves_last_valid_registry_config(tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"
    registry = _new_registry(config_path)
    _upsert_test_provider(registry)
    before = registry.public_snapshot()

    config_path.write_text('{"providers":', encoding="utf-8")

    with pytest.raises(_provider_error_type(), match="invalid|reload"):
        registry.reload()

    assert registry.public_snapshot() == before


def test_codex_oauth_preset_cannot_change_credential_destination(
    tmp_path: Path,
) -> None:
    from mcpo.services.provider_registry import CODEX_BACKEND_URL

    registry = _new_registry(tmp_path / "providers.json")

    with pytest.raises(_provider_error_type(), match="base URL cannot be changed"):
        registry.upsert_provider(
            provider_id="codex-oauth",
            display_name="Codex OAuth",
            kind="codex_oauth",
            base_url="https://attacker.example/v1",
        )
    with pytest.raises(_provider_error_type(), match="cannot store an API key"):
        registry.upsert_provider(
            provider_id="codex-oauth",
            display_name="Codex OAuth",
            kind="codex_oauth",
            base_url=CODEX_BACKEND_URL,
            api_key="must-not-be-saved",
        )


def test_codex_oauth_resolver_ignores_unsafe_saved_fields(tmp_path: Path) -> None:
    from mcpo.services.provider_registry import CODEX_BACKEND_URL

    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": [
                    {
                        "id": "codex-oauth",
                        "displayName": "Codex OAuth",
                        "kind": "openai_compatible",
                        "baseUrl": "https://attacker.example/v1",
                        "apiKey": "attacker-key",
                        "enabled": True,
                        "billing": "paid",
                        "capabilities": ["text"],
                        "models": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    provider = _new_registry(path).resolved_provider("codex-oauth")

    assert provider["kind"] == "codex_oauth"
    assert provider["baseUrl"] == CODEX_BACKEND_URL
    assert provider["apiKey"] is None
    assert provider["credentialBoundaryLocked"] is True
