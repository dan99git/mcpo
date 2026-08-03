from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from mcpo.services.codex_oauth import CODEX_BACKEND_URL, codex_oauth_status


class ProviderRegistryError(ValueError):
    """Raised when provider configuration is invalid or cannot be persisted."""


_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VALID_KINDS = {
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "minimax",
    "glm",
    "kimi",
    "codex_oauth",
    "openai_compatible",
}
_VALID_BILLING = {
    "free",
    "free_rate_limited",
    "trial_credit",
    "local",
    "paid",
    "unknown",
}
_UNSET = object()
_PROTECTED_CODEX_PROVIDER_ID = "codex-oauth"


# Presets describe connection protocols and documented account categories. They
# deliberately avoid pinning fast-moving model names. Model catalogs are fetched
# from each configured provider at runtime.
PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "codex-oauth": {
        "id": "codex-oauth",
        "displayName": "Codex OAuth",
        "kind": "codex_oauth",
        "baseUrl": CODEX_BACKEND_URL,
        "billing": "paid",
        "envKeys": [],
        "envBaseUrls": [],
        "docsUrl": "https://developers.openai.com/codex/auth/",
        "capabilities": ["text", "images", "tools", "reasoning"],
        "supportsKeyless": True,
        "credentialBoundaryLocked": True,
    },
    "openai": {
        "id": "openai",
        "displayName": "OpenAI",
        "kind": "openai",
        "baseUrl": "https://api.openai.com",
        "billing": "paid",
        "envKeys": ["OPEN_AI_API_KEY", "OPENAI_API_KEY"],
        "envBaseUrls": ["OPEN_AI_BASE_URL", "OPENAI_BASE_URL"],
        "docsUrl": "https://platform.openai.com/docs/overview",
        "capabilities": ["text", "images", "files", "tools", "reasoning"],
    },
    "openrouter": {
        "id": "openrouter",
        "displayName": "OpenRouter",
        "kind": "openrouter",
        "baseUrl": "https://openrouter.ai/api/v1",
        "billing": "free_rate_limited",
        "envKeys": ["OPENROUTER_API_KEY"],
        "envBaseUrls": ["OPENROUTER_BASE_URL"],
        "docsUrl": "https://openrouter.ai/docs/quickstart",
        "capabilities": ["text", "images", "files", "tools", "reasoning"],
        "models": [
            {
                "id": "openrouter/free",
                "label": "OpenRouter Free Router",
                "free": True,
                "billing": "free",
                "capabilities": ["text"],
                "inputModalities": ["text"],
            }
        ],
    },
    "anthropic": {
        "id": "anthropic",
        "displayName": "Anthropic",
        "kind": "anthropic",
        "baseUrl": "https://api.anthropic.com",
        "billing": "paid",
        "envKeys": ["ANTHROPIC_API_KEY"],
        "envBaseUrls": ["ANTHROPIC_BASE_URL"],
        "docsUrl": "https://docs.anthropic.com/en/api/getting-started",
        "capabilities": ["text", "images", "files", "tools", "reasoning"],
    },
    "gemini": {
        "id": "gemini",
        "displayName": "Google Gemini",
        "kind": "gemini",
        "baseUrl": "https://generativelanguage.googleapis.com",
        "billing": "free_rate_limited",
        "envKeys": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "envBaseUrls": ["GEMINI_BASE_URL", "GOOGLE_BASE_URL"],
        "docsUrl": "https://ai.google.dev/gemini-api/docs",
        "capabilities": ["text", "images", "files", "tools", "reasoning"],
    },
    "minimax": {
        "id": "minimax",
        "displayName": "MiniMax",
        "kind": "minimax",
        "baseUrl": "https://api.minimax.io/anthropic",
        "billing": "paid",
        "envKeys": ["MINIMAX_API_KEY"],
        "envBaseUrls": ["MINIMAX_BASE_URL"],
        "docsUrl": "https://platform.minimax.io/docs",
        "capabilities": ["text", "tools", "reasoning"],
    },
    "glm": {
        "id": "glm",
        "displayName": "GLM coding plan",
        "kind": "glm",
        "baseUrl": "https://open.bigmodel.cn/api/anthropic",
        "billing": "paid",
        "envKeys": ["GLM_API_KEY", "ZHIPU_API_KEY"],
        "envBaseUrls": ["GLM_BASE_URL"],
        "docsUrl": "https://docs.z.ai/",
        "capabilities": ["text", "tools", "reasoning"],
    },
    "zai": {
        "id": "zai",
        "displayName": "Z.AI",
        "kind": "openai_compatible",
        "baseUrl": "https://api.z.ai/api/paas/v4",
        "billing": "free_rate_limited",
        "envKeys": ["ZAI_API_KEY"],
        "envBaseUrls": ["ZAI_BASE_URL"],
        "docsUrl": "https://docs.z.ai/",
        "capabilities": ["text", "tools", "reasoning"],
    },
    "kimi": {
        "id": "kimi",
        "displayName": "Kimi",
        "kind": "kimi",
        "baseUrl": "https://api.moonshot.ai/v1",
        "billing": "paid",
        "envKeys": ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
        "envBaseUrls": ["KIMI_BASE_URL"],
        "docsUrl": "https://platform.moonshot.ai/docs",
        "capabilities": ["text", "tools", "reasoning"],
    },
    "groq": {
        "id": "groq",
        "displayName": "Groq",
        "kind": "openai_compatible",
        "baseUrl": "https://api.groq.com/openai/v1",
        "billing": "free_rate_limited",
        "envKeys": ["GROQ_API_KEY"],
        "envBaseUrls": ["GROQ_BASE_URL"],
        "docsUrl": "https://console.groq.com/docs/overview",
        "capabilities": ["text", "images", "tools"],
    },
    "mistral": {
        "id": "mistral",
        "displayName": "Mistral AI",
        "kind": "openai_compatible",
        "baseUrl": "https://api.mistral.ai/v1",
        "billing": "free_rate_limited",
        "envKeys": ["MISTRAL_API_KEY"],
        "envBaseUrls": ["MISTRAL_BASE_URL"],
        "docsUrl": "https://docs.mistral.ai/getting-started/quickstart/",
        "capabilities": ["text", "images", "tools"],
    },
    "ollama": {
        "id": "ollama",
        "displayName": "Ollama (local)",
        "kind": "openai_compatible",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "billing": "local",
        "envKeys": [],
        "envBaseUrls": ["OLLAMA_BASE_URL"],
        "docsUrl": "https://docs.ollama.com/openai",
        "capabilities": ["text", "images", "tools"],
        "supportsKeyless": True,
    },
    "lmstudio": {
        "id": "lmstudio",
        "displayName": "LM Studio (local)",
        "kind": "openai_compatible",
        "baseUrl": "http://127.0.0.1:1234/v1",
        "billing": "local",
        "envKeys": [],
        "envBaseUrls": ["LM_STUDIO_BASE_URL"],
        "docsUrl": "https://lmstudio.ai/docs/developer/openai-compat",
        "capabilities": ["text", "images", "tools"],
        "supportsKeyless": True,
    },
}


def _validate_provider_id(provider_id: str) -> str:
    normalized = (provider_id or "").strip().lower()
    if not _PROVIDER_ID_RE.fullmatch(normalized):
        raise ProviderRegistryError(
            "Provider id must start with a lowercase letter or number and use only lowercase letters, numbers, '.', '_' or '-'."
        )
    return normalized


def _is_loopback_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_provider_base_url(base_url: str) -> str:
    candidate = (base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ProviderRegistryError(f"Invalid provider base URL: {exc}") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderRegistryError("Provider base URL must be an absolute http or https URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderRegistryError("Provider base URL must not contain credentials.")
    if parsed.query:
        raise ProviderRegistryError("Provider base URL must not contain a query string.")
    if parsed.fragment:
        raise ProviderRegistryError("Provider base URL must not contain a fragment.")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ProviderRegistryError("Provider base URL must use https unless it targets a loopback host.")
    if port is not None and not 1 <= port <= 65535:
        raise ProviderRegistryError("Provider base URL has an invalid port.")
    return candidate


def _clean_models(provider_id: str, models: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in models or []:
        if not isinstance(raw, dict):
            raise ProviderRegistryError("Provider models must be objects.")
        model_id = str(raw.get("id") or "").strip()
        if not model_id or len(model_id) > 256:
            raise ProviderRegistryError("Provider model id must contain 1 to 256 characters.")
        if model_id in seen:
            continue
        seen.add(model_id)
        model: Dict[str, Any] = {
            "id": model_id,
            "label": str(raw.get("label") or model_id)[:256],
        }
        for key in (
            "billing",
            "free",
            "capabilities",
            "inputModalities",
            "contextWindow",
            "description",
        ):
            if key in raw:
                model[key] = copy.deepcopy(raw[key])
        cleaned.append(model)
    return cleaned


def _public_model(provider_id: str, raw: Dict[str, Any], provider_billing: str) -> Dict[str, Any]:
    model = copy.deepcopy(raw)
    model["provider"] = provider_id
    model["key"] = f"{provider_id}:{model['id']}"
    model.setdefault("billing", provider_billing)
    if "free" not in model:
        model["free"] = model.get("billing") in {"free", "free_rate_limited", "local"}
    model.setdefault("capabilities", [])
    model.setdefault("inputModalities", ["text"])
    return model


class ProviderRegistry:
    """Thread-safe provider credential and model metadata store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._providers: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            self.reload()

    def _validated_record(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = _validate_provider_id(str(raw.get("id") or raw.get("provider_id") or ""))
        display_name = str(raw.get("displayName") or raw.get("display_name") or provider_id).strip()
        if not display_name or len(display_name) > 120:
            raise ProviderRegistryError("Provider display name must contain 1 to 120 characters.")
        kind = str(raw.get("kind") or "openai_compatible").strip().lower()
        if kind not in _VALID_KINDS:
            raise ProviderRegistryError(f"Unsupported provider kind '{kind}'.")
        billing = str(raw.get("billing") or "unknown").strip().lower()
        if billing not in _VALID_BILLING:
            raise ProviderRegistryError(f"Unsupported provider billing category '{billing}'.")
        base_url = validate_provider_base_url(str(raw.get("baseUrl") or raw.get("base_url") or ""))
        api_key = raw.get("apiKey", raw.get("api_key"))
        if api_key is not None:
            api_key = str(api_key).strip()
            if not api_key:
                api_key = None
            elif len(api_key) > 8192 or "\n" in api_key or "\r" in api_key:
                raise ProviderRegistryError("Provider API key is invalid.")
        capabilities = raw.get("capabilities") or ["text"]
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise ProviderRegistryError("Provider capabilities must be a list of strings.")
        return {
            "id": provider_id,
            "displayName": display_name,
            "kind": kind,
            "baseUrl": base_url,
            "apiKey": api_key,
            "enabled": bool(raw.get("enabled", True)),
            "billing": billing,
            "capabilities": list(dict.fromkeys(capabilities)),
            "models": _clean_models(provider_id, raw.get("models")),
            "lastVerifiedAt": raw.get("lastVerifiedAt"),
        }

    def reload(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._providers = {}
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                providers = payload.get("providers")
                if not isinstance(providers, list):
                    raise ProviderRegistryError("Provider registry is invalid: 'providers' must be a list.")
                candidate: Dict[str, Dict[str, Any]] = {}
                for raw in providers:
                    if not isinstance(raw, dict):
                        raise ProviderRegistryError("Provider registry is invalid: provider entries must be objects.")
                    record = self._validated_record(raw)
                    candidate[record["id"]] = record
            except ProviderRegistryError:
                raise
            except Exception as exc:
                raise ProviderRegistryError(f"Provider registry reload failed: invalid JSON ({exc}).") from exc
            self._providers = candidate

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": 1, "providers": list(self._providers.values())},
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise ProviderRegistryError(f"Failed to save provider registry: {exc}") from exc

    def upsert_provider(
        self,
        *,
        provider_id: str,
        display_name: str,
        base_url: str,
        api_key: str | None | object = _UNSET,
        kind: str = "openai_compatible",
        enabled: bool = True,
        billing: str = "unknown",
        capabilities: Optional[List[str]] = None,
        models: Optional[List[Dict[str, Any]]] = None,
        last_verified_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_id = _validate_provider_id(provider_id)
        if normalized_id == _PROTECTED_CODEX_PROVIDER_ID:
            expected = PROVIDER_PRESETS[_PROTECTED_CODEX_PROVIDER_ID]
            if str(kind).strip().lower() != expected["kind"]:
                raise ProviderRegistryError(
                    "The Codex OAuth provider kind cannot be changed."
                )
            if validate_provider_base_url(base_url) != expected["baseUrl"]:
                raise ProviderRegistryError(
                    "The Codex OAuth provider base URL cannot be changed."
                )
            if api_key is not _UNSET and api_key:
                raise ProviderRegistryError(
                    "The Codex OAuth provider cannot store an API key."
                )
            kind = expected["kind"]
            base_url = expected["baseUrl"]
            api_key = None
        with self._lock:
            existing = self._providers.get(normalized_id, {})
            candidate = {
                "id": normalized_id,
                "displayName": display_name,
                "baseUrl": base_url,
                "kind": kind,
                "enabled": enabled,
                "billing": billing,
                "capabilities": capabilities if capabilities is not None else existing.get("capabilities", ["text"]),
                "models": models if models is not None else existing.get("models", []),
                "lastVerifiedAt": last_verified_at if last_verified_at is not None else existing.get("lastVerifiedAt"),
                "apiKey": existing.get("apiKey") if api_key is _UNSET else api_key,
            }
            record = self._validated_record(candidate)
            self._providers[normalized_id] = record
            try:
                self._save()
            except Exception:
                if existing:
                    self._providers[normalized_id] = existing
                else:
                    self._providers.pop(normalized_id, None)
                raise
            return self.public_provider(normalized_id)

    def delete_provider(self, provider_id: str) -> bool:
        normalized_id = _validate_provider_id(provider_id)
        with self._lock:
            existing = self._providers.pop(normalized_id, None)
            if existing is None:
                return False
            try:
                self._save()
            except Exception:
                self._providers[normalized_id] = existing
                raise
            return True

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = _validate_provider_id(provider_id)
        with self._lock:
            record = self._providers.get(normalized_id)
            return copy.deepcopy(record) if record else None

    def public_provider(self, provider_id: str) -> Dict[str, Any]:
        record = self.get_provider(provider_id)
        if record is None:
            raise ProviderRegistryError(f"Provider '{provider_id}' is not configured.")
        api_key = record.pop("apiKey", None)
        record["hasApiKey"] = bool(api_key)
        record["keySource"] = "saved" if api_key else "none"
        record["models"] = [
            _public_model(record["id"], model, record["billing"])
            for model in record.get("models", [])
        ]
        return record

    def public_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": 1,
                "providers": [self.public_provider(provider_id) for provider_id in self._providers],
            }

    def resolved_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        normalized_id = _validate_provider_id(provider_id)
        preset = copy.deepcopy(PROVIDER_PRESETS.get(normalized_id) or {})
        saved = self.get_provider(normalized_id)
        if not preset and not saved:
            return None

        record: Dict[str, Any] = {
            "id": normalized_id,
            "displayName": preset.get("displayName", normalized_id),
            "kind": preset.get("kind", "openai_compatible"),
            "baseUrl": preset.get("baseUrl", ""),
            "billing": preset.get("billing", "unknown"),
            "capabilities": copy.deepcopy(preset.get("capabilities", ["text"])),
            "models": copy.deepcopy(preset.get("models", [])),
            "enabled": True,
            "docsUrl": preset.get("docsUrl"),
            "supportsKeyless": bool(preset.get("supportsKeyless", False)),
            "credentialBoundaryLocked": bool(
                preset.get("credentialBoundaryLocked", False)
            ),
            "isPreset": bool(preset),
            "isSaved": bool(saved),
            "lastVerifiedAt": None,
            "baseUrlSource": "preset" if preset else "saved",
        }
        if saved:
            saved_fields = [
                "displayName",
                "billing",
                "capabilities",
                "models",
                "enabled",
                "lastVerifiedAt",
            ]
            if normalized_id != _PROTECTED_CODEX_PROVIDER_ID:
                saved_fields.extend(["kind", "baseUrl"])
            for key in saved_fields:
                if key in saved:
                    record[key] = copy.deepcopy(saved[key])
            if normalized_id != _PROTECTED_CODEX_PROVIDER_ID:
                record["baseUrlSource"] = "saved"

        saved_key = (
            saved.get("apiKey")
            if saved and normalized_id != _PROTECTED_CODEX_PROVIDER_ID
            else None
        )
        env_key = None
        env_key_name = None
        for name in preset.get("envKeys", []):
            value = os.getenv(name)
            if value:
                env_key = value
                env_key_name = name
                break

        preset_base = preset.get("baseUrl")
        env_base = None
        for name in preset.get("envBaseUrls", []):
            value = os.getenv(name)
            if value:
                env_base = validate_provider_base_url(value)
                break
        if not saved or saved.get("baseUrl") == preset_base:
            if env_base:
                record["baseUrl"] = env_base
                record["baseUrlSource"] = "environment"

        if saved_key:
            record["apiKey"] = saved_key
            record["keySource"] = "saved"
        elif env_key and (not saved or saved.get("baseUrl") in {preset_base, env_base}):
            # Never attach an environment-held credential to a custom override
            # host. A key must be entered explicitly for that destination.
            record["apiKey"] = env_key
            record["keySource"] = "environment"
            record["keyEnvironment"] = env_key_name
        else:
            record["apiKey"] = None
            record["keySource"] = "none"

        parsed_base = urlsplit(record["baseUrl"])
        record["supportsKeyless"] = bool(
            record["supportsKeyless"]
            or (
                parsed_base.scheme == "http"
                and _is_loopback_host(parsed_base.hostname)
            )
        )
        if record["kind"] == "codex_oauth":
            oauth_status = codex_oauth_status()
            record["oauthReady"] = oauth_status["ready"]
            record["configured"] = bool(record["enabled"] and oauth_status["ready"])
        else:
            record["configured"] = bool(
                record["enabled"]
                and (
                    record["apiKey"]
                    or (record["supportsKeyless"] and record["isSaved"])
                )
            )
        return record

    def resolved_providers(self, *, include_unconfigured: bool = False) -> List[Dict[str, Any]]:
        provider_ids = list(PROVIDER_PRESETS)
        with self._lock:
            provider_ids.extend(pid for pid in self._providers if pid not in PROVIDER_PRESETS)
        resolved: List[Dict[str, Any]] = []
        for provider_id in provider_ids:
            provider = self.resolved_provider(provider_id)
            if provider and (include_unconfigured or provider["configured"]):
                resolved.append(provider)
        return resolved

    def effective_public_snapshot(self) -> Dict[str, Any]:
        providers: List[Dict[str, Any]] = []
        for resolved in self.resolved_providers(include_unconfigured=True):
            api_key = resolved.pop("apiKey", None)
            resolved.pop("keyEnvironment", None)
            resolved["hasApiKey"] = bool(api_key)
            resolved["models"] = [
                _public_model(resolved["id"], model, resolved["billing"])
                for model in resolved.get("models", [])
            ]
            providers.append(resolved)
        return {"version": 1, "providers": providers}


_registry_lock = threading.Lock()
_registry_by_path: Dict[str, ProviderRegistry] = {}


def get_provider_registry(path: str | Path | None = None) -> ProviderRegistry:
    selected = Path(path or os.getenv("MCPO_PROVIDER_CONFIG_PATH", ".mcpo/providers.json"))
    key = str(selected.resolve())
    with _registry_lock:
        registry = _registry_by_path.get(key)
        if registry is None:
            registry = ProviderRegistry(selected)
            _registry_by_path[key] = registry
        return registry
