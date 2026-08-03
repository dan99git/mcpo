from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from mcpo.services.provider_models import test_provider_connection
from mcpo.services.provider_registry import (
    PROVIDER_PRESETS,
    ProviderRegistry,
    ProviderRegistryError,
    get_provider_registry,
)


router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderModelInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(..., min_length=1, max_length=256)
    label: Optional[str] = Field(None, max_length=256)
    billing: Optional[str] = None
    free: Optional[bool] = None
    capabilities: List[str] = Field(default_factory=list, max_length=20)
    input_modalities: List[str] = Field(
        default_factory=lambda: ["text"],
        alias="inputModalities",
        max_length=10,
    )
    context_window: Optional[int] = Field(None, alias="contextWindow", gt=0)
    description: Optional[str] = Field(None, max_length=1000)


class ProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    display_name: Optional[str] = Field(None, alias="displayName", min_length=1, max_length=120)
    kind: Optional[str] = None
    base_url: Optional[str] = Field(None, alias="baseUrl", min_length=1, max_length=2048)
    api_key: Optional[str] = Field(None, alias="apiKey", max_length=8192)
    clear_api_key: bool = Field(False, alias="clearApiKey")
    enabled: Optional[bool] = None
    billing: Optional[str] = None
    capabilities: Optional[List[str]] = Field(None, max_length=20)
    models: Optional[List[ProviderModelInput]] = Field(None, max_length=500)


def _registry(request: Request) -> ProviderRegistry:
    configured = getattr(request.app.state, "provider_registry", None)
    return configured if isinstance(configured, ProviderRegistry) else get_provider_registry()


def _reject_read_only(request: Request) -> None:
    if getattr(request.app.state, "read_only_mode", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only mode enabled",
        )


@router.get("")
async def list_providers(request: Request) -> Dict[str, Any]:
    snapshot = _registry(request).effective_public_snapshot()
    return {"ok": True, **snapshot}


@router.get("/presets")
async def list_provider_presets() -> Dict[str, Any]:
    presets: List[Dict[str, Any]] = []
    for raw in PROVIDER_PRESETS.values():
        preset = {
            key: value
            for key, value in raw.items()
            if key not in {"envKeys", "envBaseUrls"}
        }
        presets.append(preset)
    return {"ok": True, "providers": presets}


@router.put("/{provider_id}")
async def save_provider(
    provider_id: str,
    payload: ProviderUpsertRequest,
    request: Request,
) -> Dict[str, Any]:
    _reject_read_only(request)
    registry = _registry(request)
    try:
        current = registry.resolved_provider(provider_id)
        if current is None and (not payload.display_name or not payload.base_url):
            raise ProviderRegistryError(
                "Custom providers require displayName and baseUrl."
            )
        current = current or {}
        models = None
        if payload.models is not None:
            models = [model.model_dump(by_alias=True, exclude_none=True) for model in payload.models]
        kwargs: Dict[str, Any] = {
            "provider_id": provider_id,
            "display_name": payload.display_name or current.get("displayName") or provider_id,
            "kind": payload.kind or current.get("kind") or "openai_compatible",
            "base_url": payload.base_url or current.get("baseUrl") or "",
            "enabled": payload.enabled if payload.enabled is not None else current.get("enabled", True),
            "billing": payload.billing or current.get("billing") or "unknown",
            "capabilities": payload.capabilities if payload.capabilities is not None else current.get("capabilities", ["text"]),
            "models": models if models is not None else current.get("models", []),
        }
        if payload.clear_api_key:
            kwargs["api_key"] = None
        elif "api_key" in payload.model_fields_set:
            kwargs["api_key"] = payload.api_key
        registry.upsert_provider(**kwargs)
        provider = registry.resolved_provider(provider_id)
        public = next(
            item
            for item in registry.effective_public_snapshot()["providers"]
            if item["id"] == provider_id.lower()
        )
        return {"ok": True, "provider": public}
    except ProviderRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str, request: Request) -> Dict[str, Any]:
    _reject_read_only(request)
    registry = _registry(request)
    try:
        removed = registry.delete_provider(provider_id)
    except ProviderRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return {"ok": True, "removed": removed}


@router.post("/{provider_id}/test")
async def probe_provider(provider_id: str, request: Request) -> Dict[str, Any]:
    _reject_read_only(request)
    registry = _registry(request)
    try:
        result = await test_provider_connection(provider_id, registry)
        models = result["models"]
        verification_mode = result["verification_mode"]
        live_verified = (
            verification_mode == "live"
            and result["status"] == "connected"
            and result["verified"] is True
        )
        current = registry.resolved_provider(provider_id)
        if current is None:
            raise ProviderRegistryError(f"Provider '{provider_id}' is not configured.")
        verified_at = datetime.now(timezone.utc).isoformat() if live_verified else None
        update_kwargs: Dict[str, Any] = {
            "provider_id": provider_id,
            "display_name": current["displayName"],
            "kind": current["kind"],
            "base_url": current["baseUrl"],
            "enabled": current["enabled"],
            "billing": current["billing"],
            "capabilities": current["capabilities"],
            "models": models,
        }
        if verified_at is not None:
            update_kwargs["last_verified_at"] = verified_at
        registry.upsert_provider(**update_kwargs)
        response: Dict[str, Any] = {
            "ok": True,
            "providerId": provider_id,
            "verificationMode": verification_mode,
            "status": "connected" if live_verified else "not_verified",
            "verified": live_verified,
            "message": (
                "Provider connected."
                if live_verified
                else "Configuration saved; live connection not verified."
            ),
            "modelCount": len(models),
            "models": models,
        }
        if verified_at is not None:
            response["verifiedAt"] = verified_at
        return response
    except (ProviderRegistryError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Provider returned HTTP {exc.response.status_code} while listing models.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Provider connection failed: {type(exc).__name__}.",
        ) from exc
