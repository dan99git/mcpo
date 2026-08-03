from __future__ import annotations

import asyncio
import copy
import logging
import re
from typing import Any, Dict, List, Literal, Optional, TypedDict

import httpx

from mcpo.providers.glm import get_glm_models
from mcpo.providers.kimi import get_kimi_models
from mcpo.providers.minimax import get_minimax_models
from mcpo.services.codex_oauth import load_codex_models
from mcpo.services.provider_registry import ProviderRegistry, get_provider_registry


logger = logging.getLogger(__name__)


_MODALITY_CAPABILITIES = {"images", "files", "audio", "video"}
_OPENAI_MULTIMODAL_MODEL_PATTERNS = (
    re.compile(
        r"^gpt-5(?:\.\d+)?(?:-(?:mini|nano|pro|sol|terra|luna))?"
        r"(?:-\d{4}-\d{2}-\d{2})?$"
    ),
    re.compile(
        r"^gpt-4\.1(?:-(?:mini|nano))?(?:-\d{4}-\d{2}-\d{2})?$"
    ),
    re.compile(r"^gpt-4o(?:-mini)?(?:-\d{4}-\d{2}-\d{2})?$"),
)
_ANTHROPIC_MULTIMODAL_MODEL_PATTERNS = (
    re.compile(
        r"^claude-(?:opus|sonnet|haiku)-4"
        r"(?:-(?:1|5|6|7|8))?(?:-\d{8})?$"
    ),
    re.compile(r"^claude-(?:fable|sonnet|mythos)-5$"),
    re.compile(r"^claude-mythos-preview$"),
)
_GEMINI_MULTIMODAL_MODEL_BASES = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3.1-pro",
    "gemini-3.1-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
)
_GEMINI_VERSION_SUFFIX = re.compile(
    r"^-(?:\d{3}|preview(?:-\d{2}-\d{4})?|exp(?:-\d{2}-\d{4})?)$"
)


class ProviderConnectionTestResult(TypedDict):
    models: List[Dict[str, Any]]
    verification_mode: Literal["live", "catalog_only"]
    status: Literal["connected", "not_verified"]
    verified: bool


def _format_model_label(model_id: str) -> str:
    tail = model_id.split("/")[-1]
    return tail.replace("-", " ").replace("_", " ").title()


def _model_id(provider: Dict[str, Any], entry: Dict[str, Any]) -> str:
    model_id = str(entry.get("id") or entry.get("name") or "").lower()
    if provider.get("kind") == "gemini" and model_id.startswith("models/"):
        return model_id.split("/", 1)[1]
    return model_id


def _explicit_input_modalities(entry: Dict[str, Any]) -> Optional[List[str]]:
    architecture = (
        entry.get("architecture")
        if isinstance(entry.get("architecture"), dict)
        else {}
    )
    if "input_modalities" in architecture:
        raw_modalities = architecture.get("input_modalities")
    elif "inputModalities" in entry:
        raw_modalities = entry.get("inputModalities")
    else:
        return None

    if isinstance(raw_modalities, str):
        raw_modalities = [raw_modalities]
    if not isinstance(raw_modalities, (list, tuple, set)):
        return ["text"]
    modalities = [str(item).lower() for item in raw_modalities if item]
    return list(dict.fromkeys(modalities or ["text"]))


def _known_model_input_modalities(
    provider: Dict[str, Any], entry: Dict[str, Any]
) -> List[str]:
    kind = provider.get("kind")
    model_id = _model_id(provider, entry)
    if kind == "openai" and any(
        pattern.fullmatch(model_id)
        for pattern in _OPENAI_MULTIMODAL_MODEL_PATTERNS
    ):
        return ["text", "image", "file"]
    if kind == "anthropic" and any(
        pattern.fullmatch(model_id)
        for pattern in _ANTHROPIC_MULTIMODAL_MODEL_PATTERNS
    ):
        return ["text", "image", "file"]
    if kind == "gemini":
        if model_id in {"gemini-flash-latest", "gemini-pro-latest"}:
            return ["text", "image", "file"]
        for base in _GEMINI_MULTIMODAL_MODEL_BASES:
            if model_id == base or (
                model_id.startswith(base)
                and _GEMINI_VERSION_SUFFIX.fullmatch(model_id[len(base) :])
            ):
                return ["text", "image", "file"]
    return ["text"]


def _model_capabilities(
    provider: Dict[str, Any], entry: Dict[str, Any]
) -> tuple[List[str], List[str]]:
    modalities = _explicit_input_modalities(entry)
    if modalities is None:
        modalities = _known_model_input_modalities(provider, entry)
    supported = {
        str(item).lower() for item in entry.get("supported_parameters") or []
    }
    capabilities = {"text"}
    if "image" in modalities:
        capabilities.add("images")
    if "file" in modalities or "pdf" in modalities:
        capabilities.add("files")
    if "audio" in modalities:
        capabilities.add("audio")
    if "video" in modalities:
        capabilities.add("video")
    if "tools" in supported or "tool_choice" in supported:
        capabilities.add("tools")
    if "reasoning" in supported or "include_reasoning" in supported:
        capabilities.add("reasoning")
    if "response_format" in supported or "structured_outputs" in supported:
        capabilities.add("structured_output")
    if not entry.get("supported_parameters") and not entry.get("architecture"):
        capabilities.update(
            str(item)
            for item in provider.get("capabilities", [])
            if str(item) not in _MODALITY_CAPABILITIES
        )
    return sorted(capabilities), list(dict.fromkeys(modalities or ["text"]))


def _is_zero_price(entry: Dict[str, Any]) -> bool:
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return False
    values = [pricing.get("prompt"), pricing.get("completion")]
    if any(value is None for value in values):
        return False
    try:
        return all(float(value) == 0 for value in values)
    except (TypeError, ValueError):
        return False


def normalize_model(
    provider: Dict[str, Any], entry: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    raw_id = entry.get("id") or entry.get("name")
    if not raw_id:
        return None
    model_id = str(raw_id)
    if provider["kind"] == "gemini" and model_id.startswith("models/"):
        model_id = model_id.split("/", 1)[1]
    if provider["kind"] == "gemini":
        methods = entry.get("supportedGenerationMethods") or []
        if methods and "generateContent" not in methods:
            return None

    capabilities, modalities = _model_capabilities(provider, entry)
    billing = str(entry.get("billing") or provider.get("billing") or "unknown")
    free = bool(entry.get("free", False) or _is_zero_price(entry))
    if not free and billing in {"free", "free_rate_limited", "local"}:
        free = True
    label = (
        entry.get("label")
        or entry.get("display_name")
        or entry.get("displayName")
        or entry.get("name")
        or _format_model_label(model_id)
    )
    result: Dict[str, Any] = {
        "id": model_id,
        "key": f"{provider['id']}:{model_id}",
        "label": str(label),
        "provider": provider["id"],
        "providerLabel": provider["displayName"],
        "billing": billing,
        "free": free,
        "capabilities": capabilities,
        "inputModalities": modalities,
    }
    context_window = (
        entry.get("context_length")
        or entry.get("inputTokenLimit")
        or entry.get("contextWindow")
    )
    if context_window is not None:
        result["contextWindow"] = context_window
    description = entry.get("description")
    if description:
        result["description"] = str(description)
    return result


def _headers_for(provider: Dict[str, Any]) -> Dict[str, str]:
    api_key = provider.get("apiKey")
    kind = provider["kind"]
    if not api_key:
        return {}
    if kind == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    if kind == "gemini":
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _models_url(provider: Dict[str, Any]) -> str:
    base_url = provider["baseUrl"].rstrip("/")
    if provider["kind"] in {"openai", "anthropic"}:
        return f"{base_url}/v1/models"
    if provider["kind"] == "gemini":
        return f"{base_url}/v1beta/models"
    return f"{base_url}/models"


def _static_models(
    provider: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    kind = provider["kind"]
    raw: Optional[List[Dict[str, Any]]] = None
    if kind == "codex_oauth":
        raw = load_codex_models()
    elif kind == "minimax":
        raw = get_minimax_models()
    elif kind == "glm":
        raw = get_glm_models()
    elif kind == "kimi":
        raw = get_kimi_models()
    if raw is None:
        return None
    return [
        model
        for entry in raw
        if (model := normalize_model(provider, entry))
    ]


async def fetch_provider_models(
    provider: Dict[str, Any],
    *,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    static = _static_models(provider)
    if static is not None:
        return static
    fallback = [
        model
        for entry in provider.get("models", [])
        if (model := normalize_model(provider, entry))
    ]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _models_url(provider),
                headers=_headers_for(provider),
            )
            response.raise_for_status()
            payload = response.json()
        raw_models = (
            payload.get("models")
            if provider["kind"] == "gemini"
            else payload.get("data")
        )
        if not isinstance(raw_models, list):
            return fallback
        discovered = [
            model
            for entry in raw_models
            if isinstance(entry, dict)
            and (model := normalize_model(provider, entry))
        ]
        return discovered or fallback
    except Exception as exc:
        logger.warning(
            "Model discovery failed for provider %s: %s",
            provider["id"],
            exc,
        )
        return fallback


async def test_provider_connection(
    provider_id: str,
    registry: Optional[ProviderRegistry] = None,
) -> ProviderConnectionTestResult:
    selected_registry = registry or get_provider_registry()
    provider = selected_registry.resolved_provider(provider_id)
    if not provider or not provider.get("configured"):
        raise ValueError(f"Provider '{provider_id}' is not configured.")
    static = _static_models(provider)
    if static is not None:
        return {
            "models": static,
            "verification_mode": "catalog_only",
            "status": "not_verified",
            "verified": False,
        }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            _models_url(provider),
            headers=_headers_for(provider),
        )
        response.raise_for_status()
        payload = response.json()
    raw_models = (
        payload.get("models")
        if provider["kind"] == "gemini"
        else payload.get("data")
    )
    if not isinstance(raw_models, list):
        raise ValueError("Provider returned an invalid model catalog.")
    models = [
        model
        for entry in raw_models
        if isinstance(entry, dict)
        and (model := normalize_model(provider, entry))
    ]
    return {
        "models": models,
        "verification_mode": "live",
        "status": "connected",
        "verified": True,
    }


async def list_provider_models(
    registry: Optional[ProviderRegistry] = None,
) -> List[Dict[str, Any]]:
    selected_registry = registry or get_provider_registry()
    providers = selected_registry.resolved_providers()
    if not providers:
        return []
    results = await asyncio.gather(
        *(fetch_provider_models(provider) for provider in providers),
        return_exceptions=True,
    )
    combined: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        if isinstance(result, BaseException):
            continue
        for model in result:
            identity = (model["provider"], model["id"])
            if identity in seen:
                continue
            seen.add(identity)
            combined.append(copy.deepcopy(model))
    combined.sort(
        key=lambda model: (
            0 if model.get("free") else 1,
            str(model.get("providerLabel", model["provider"])).lower(),
            str(model.get("label", model["id"])).lower(),
        )
    )
    return combined
