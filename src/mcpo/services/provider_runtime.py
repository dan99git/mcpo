from __future__ import annotations

from typing import Any, Optional

from mcpo.providers.anthropic import AnthropicClient
from mcpo.providers.glm import GLMClient
from mcpo.providers.google import GoogleClient
from mcpo.providers.kimi import KimiClient
from mcpo.providers.minimax import MiniMaxClient
from mcpo.providers.openai import OpenAIClient
from mcpo.providers.openai_compatible import OpenAICompatibleClient
from mcpo.providers.openrouter import OpenRouterClient
from mcpo.services.provider_registry import ProviderRegistry, get_provider_registry


class ProviderConfigurationError(ValueError):
    """Raised when a chat provider is missing, disabled, or unconfigured."""


def create_provider_client(
    provider_id: str,
    registry: Optional[ProviderRegistry] = None,
) -> Any:
    selected_registry = registry or get_provider_registry()
    provider = selected_registry.resolved_provider(provider_id)
    if provider is None:
        raise ProviderConfigurationError(f"Unknown provider '{provider_id}'.")
    if not provider.get("enabled", True):
        raise ProviderConfigurationError(f"Provider '{provider_id}' is disabled.")
    if not provider.get("configured"):
        raise ProviderConfigurationError(
            f"Provider '{provider_id}' needs an API key or an enabled keyless local configuration."
        )

    kind = provider["kind"]
    api_key = provider.get("apiKey")
    base_url = provider["baseUrl"]

    if kind == "openai":
        return OpenAIClient(api_key=api_key, base_url=base_url)
    if kind == "openrouter":
        return OpenRouterClient(api_key=api_key, base_url=base_url)
    if kind == "anthropic":
        return AnthropicClient(api_key=api_key, base_url=base_url)
    if kind == "gemini":
        return GoogleClient(api_key=api_key, base_url=base_url)

    # These clients select between their regular and coding-plan transports
    # from existing environment settings. Preserve that behavior unless the
    # user explicitly saved or supplied a base URL.
    optional_base = (
        base_url
        if provider.get("baseUrlSource") in {"saved", "environment"}
        else None
    )
    if kind == "minimax":
        return MiniMaxClient(api_key=api_key, base_url=optional_base)
    if kind == "glm":
        return GLMClient(api_key=api_key, base_url=optional_base)
    if kind == "kimi":
        return KimiClient(api_key=api_key, base_url=optional_base)
    if kind == "openai_compatible":
        return OpenAICompatibleClient(api_key=api_key, base_url=base_url)
    raise ProviderConfigurationError(
        f"Provider '{provider_id}' uses unsupported kind '{kind}'."
    )


def provider_capabilities(
    provider_id: str,
    registry: Optional[ProviderRegistry] = None,
) -> set[str]:
    selected_registry = registry or get_provider_registry()
    provider = selected_registry.resolved_provider(provider_id)
    if provider is None:
        return set()
    return {str(item) for item in provider.get("capabilities", [])}
