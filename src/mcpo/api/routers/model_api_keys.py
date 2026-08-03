from __future__ import annotations

import base64
import hmac
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from mcpo.services.codex_oauth import codex_oauth_status
from mcpo.services.model_api_keys import (
    CODEX_OAUTH_PROVIDER_ID,
    MODEL_API_KEY_SCOPES,
    ModelAPIKeyStore,
    ModelAPIKeyStoreError,
    get_model_api_key_store,
)


router = APIRouter(
    prefix=f"/providers/{CODEX_OAUTH_PROVIDER_ID}/access-keys",
    tags=["model-api-keys"],
)


class ModelAPIKeyCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    scopes: List[str] = Field(
        default_factory=lambda: sorted(MODEL_API_KEY_SCOPES),
        min_length=1,
        max_length=len(MODEL_API_KEY_SCOPES),
    )
    expires_at: Optional[str] = Field(None, alias="expiresAt")


class ModelAPIKeyEnforcementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(...)


def _store(request: Request) -> ModelAPIKeyStore:
    configured = getattr(request.app.state, "model_api_key_store", None)
    return (
        configured
        if isinstance(configured, ModelAPIKeyStore)
        else get_model_api_key_store()
    )


def _require_admin(request: Request) -> None:
    api_key = getattr(request.app.state, "api_key", None) or os.getenv("MCPO_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configure the MCPO admin API key before managing model API keys.",
        )

    authorization = request.headers.get("Authorization") or ""
    supplied: Optional[str] = None
    if authorization.startswith("Bearer "):
        supplied = authorization[7:]
    elif authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:]).decode("utf-8")
            _, supplied = decoded.split(":", 1)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Basic Authentication format",
                headers={"WWW-Authenticate": "Bearer, Basic"},
            ) from exc
    elif not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported authorization method",
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )

    if supplied is None or not hmac.compare_digest(supplied, api_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    request.state.auth_principal = {"kind": "admin_api_key"}


def _reject_read_only(request: Request) -> None:
    if getattr(request.app.state, "read_only_mode", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only mode enabled",
        )


@router.get("")
async def list_model_api_keys(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        from mcpo.services.usage import get_usage_recorder

        store = _store(request)
        usage = get_usage_recorder().snapshot()
        keys = store.list_keys()
        for record in keys:
            record["usage"] = usage.get(record["id"])
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "oauth": codex_oauth_status(),
            "enforced": store.enforcement_enabled(),
            "keys": keys,
            # Usage counters are in-memory, per worker process, reset on restart.
            "usageScope": "process",
        }
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_model_api_key(
    payload: ModelAPIKeyCreateRequest,
    request: Request,
) -> Dict[str, Any]:
    _require_admin(request)
    _reject_read_only(request)
    oauth_status = codex_oauth_status()
    if not oauth_status["ready"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=oauth_status["detail"],
        )
    try:
        key, record = _store(request).create_key(
            name=payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "key": key,
            "record": record,
        }
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post("/{key_id}/revoke")
async def revoke_model_api_key(
    key_id: str,
    request: Request,
) -> Dict[str, Any]:
    _require_admin(request)
    _reject_read_only(request)
    try:
        record = _store(request).revoke_key(key_id)
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "record": record,
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model API key '{key_id}' was not found.",
        ) from exc
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("/{key_id}/rotate")
async def rotate_model_api_key(
    key_id: str,
    request: Request,
) -> Dict[str, Any]:
    """Reissue a key's secret. Old token stops working immediately; the new
    plaintext token is returned once."""
    _require_admin(request)
    _reject_read_only(request)
    try:
        key, record = _store(request).rotate_key(key_id)
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "key": key,
            "record": record,
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model API key '{key_id}' was not found.",
        ) from exc
    except ModelAPIKeyStoreError as exc:
        # Business-rule refusal (e.g. rotating a revoked key) -> 409 Conflict.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.delete("/{key_id}")
async def delete_model_api_key(
    key_id: str,
    request: Request,
) -> Dict[str, Any]:
    """Purge a key record entirely (no tombstone left in the registry)."""
    _require_admin(request)
    _reject_read_only(request)
    try:
        record = _store(request).delete_key(key_id)
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "record": record,
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model API key '{key_id}' was not found.",
        ) from exc
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("/lockdown")
async def lockdown_model_api_keys(request: Request) -> Dict[str, Any]:
    """Panic button: revoke every active key AND enable enforcement in one call.

    After this, all existing client tokens are dead and /v1 requires a fresh key
    (or the admin key). Irreversible for the revoked keys — issue new ones."""
    _require_admin(request)
    _reject_read_only(request)
    try:
        store = _store(request)
        revoked = store.revoke_all_active()
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "revoked": revoked,
            "enforced": store.enforcement_enabled(),
        }
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post("/enforcement")
async def set_model_api_key_enforcement(
    payload: ModelAPIKeyEnforcementRequest,
    request: Request,
) -> Dict[str, Any]:
    """Explicitly turn /v1 key enforcement on or off.

    enabled=true with zero keys LOCKS DOWN /v1 (closes the open-by-default gap);
    enabled=false unlatches enforcement that create_key() turned on."""
    _require_admin(request)
    _reject_read_only(request)
    try:
        enforced = _store(request).set_enforcement(payload.enabled)
        return {
            "ok": True,
            "providerId": CODEX_OAUTH_PROVIDER_ID,
            "enforced": enforced,
        }
    except ModelAPIKeyStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
