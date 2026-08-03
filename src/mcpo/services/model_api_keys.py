from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


CODEX_OAUTH_PROVIDER_ID = "codex-oauth"
MODEL_API_KEY_PREFIX = "mcpo_codex_"
MODEL_API_KEY_SCOPES = frozenset({"models:read", "responses:write"})
_KEY_ID_RE = re.compile(r"^[a-f0-9]{12}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_LAST_USED_WRITE_INTERVAL_SECONDS = 60


class ModelAPIKeyStoreError(RuntimeError):
    """Raised when the model API key registry is invalid or cannot be saved."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Any, field_name: str) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModelAPIKeyStoreError(f"Model API key {field_name} is invalid.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelAPIKeyStoreError(
            f"Model API key {field_name} is invalid."
        ) from exc
    if parsed.tzinfo is None:
        raise ModelAPIKeyStoreError(
            f"Model API key {field_name} must include a timezone."
        )
    return parsed.astimezone(timezone.utc)


def _clean_scopes(scopes: Iterable[str]) -> list[str]:
    cleaned = list(dict.fromkeys(str(scope).strip() for scope in scopes if str(scope).strip()))
    if not cleaned:
        raise ModelAPIKeyStoreError("At least one model API key scope is required.")
    unsupported = sorted(set(cleaned) - MODEL_API_KEY_SCOPES)
    if unsupported:
        raise ModelAPIKeyStoreError(
            f"Unsupported model API key scope: {', '.join(unsupported)}"
        )
    return cleaned


class ModelAPIKeyStore:
    """Persistent, provider-bound client keys for the model API."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._enforced = False
        self._keys: Dict[str, Dict[str, Any]] = {}
        if self.path.exists():
            self.reload()

    @contextmanager
    def _process_lock(self):
        """Serialize read-modify-write cycles across worker processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        handle = open(lock_path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _reload_if_present(self) -> None:
        if self.path.exists():
            self.reload()

    def _validated_record(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        key_id = str(raw.get("id") or "").strip().lower()
        if not _KEY_ID_RE.fullmatch(key_id):
            raise ModelAPIKeyStoreError("Model API key id is invalid.")
        name = str(raw.get("name") or "").strip()
        if not name or len(name) > 80 or "\n" in name or "\r" in name:
            raise ModelAPIKeyStoreError(
                "Model API key name must contain 1 to 80 characters."
            )
        provider_id = str(raw.get("providerId") or "").strip().lower()
        if provider_id != CODEX_OAUTH_PROVIDER_ID:
            raise ModelAPIKeyStoreError("Model API key provider is invalid.")
        prefix = str(raw.get("prefix") or "").strip()
        expected_prefix = f"{MODEL_API_KEY_PREFIX}{key_id}"
        if prefix != expected_prefix:
            raise ModelAPIKeyStoreError("Model API key prefix is invalid.")
        secret_hash = str(raw.get("secretHash") or "").strip().lower()
        if not _HASH_RE.fullmatch(secret_hash):
            raise ModelAPIKeyStoreError("Model API key verifier is invalid.")
        created_at = _parse_timestamp(raw.get("createdAt"), "createdAt")
        if created_at is None:
            raise ModelAPIKeyStoreError("Model API key createdAt is required.")
        expires_at = _parse_timestamp(raw.get("expiresAt"), "expiresAt")
        last_used_at = _parse_timestamp(raw.get("lastUsedAt"), "lastUsedAt")
        revoked_at = _parse_timestamp(raw.get("revokedAt"), "revokedAt")
        scopes = raw.get("scopes")
        if not isinstance(scopes, list):
            raise ModelAPIKeyStoreError("Model API key scopes must be a list.")
        return {
            "id": key_id,
            "name": name,
            "prefix": prefix,
            "providerId": provider_id,
            "secretHash": secret_hash,
            "scopes": _clean_scopes(scopes),
            "createdAt": _timestamp(created_at),
            "expiresAt": _timestamp(expires_at) if expires_at else None,
            "lastUsedAt": _timestamp(last_used_at) if last_used_at else None,
            "revokedAt": _timestamp(revoked_at) if revoked_at else None,
        }

    def reload(self) -> None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("version") != 1:
                    raise ModelAPIKeyStoreError("Model API key registry version is invalid.")
                if not isinstance(payload.get("enforced"), bool):
                    raise ModelAPIKeyStoreError(
                        "Model API key registry enforcement state is invalid."
                    )
                raw_keys = payload.get("keys")
                if not isinstance(raw_keys, list):
                    raise ModelAPIKeyStoreError(
                        "Model API key registry keys must be a list."
                    )
                candidate: Dict[str, Dict[str, Any]] = {}
                for raw in raw_keys:
                    if not isinstance(raw, dict):
                        raise ModelAPIKeyStoreError(
                            "Model API key registry entries must be objects."
                        )
                    record = self._validated_record(raw)
                    if record["id"] in candidate:
                        raise ModelAPIKeyStoreError(
                            "Model API key registry contains duplicate ids."
                        )
                    candidate[record["id"]] = record
            except ModelAPIKeyStoreError:
                raise
            except Exception as exc:
                raise ModelAPIKeyStoreError(
                    f"Model API key registry reload failed: {exc}"
                ) from exc
            self._enforced = payload["enforced"]
            self._keys = candidate

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
                    {
                        "version": 1,
                        "enforced": self._enforced,
                        "keys": list(self._keys.values()),
                    },
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
            raise ModelAPIKeyStoreError(
                f"Failed to save model API key registry: {exc}"
            ) from exc

    @staticmethod
    def _status(record: Dict[str, Any], now: Optional[datetime] = None) -> str:
        if record.get("revokedAt"):
            return "revoked"
        expires_at = _parse_timestamp(record.get("expiresAt"), "expiresAt")
        if expires_at and expires_at <= (now or _utcnow()):
            return "expired"
        return "active"

    def _public_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record["id"],
            "name": record["name"],
            "prefix": record["prefix"],
            "providerId": record["providerId"],
            "scopes": list(record["scopes"]),
            "createdAt": record["createdAt"],
            "expiresAt": record.get("expiresAt"),
            "lastUsedAt": record.get("lastUsedAt"),
            "revokedAt": record.get("revokedAt"),
            "status": self._status(record),
        }

    def enforcement_enabled(self) -> bool:
        with self._lock:
            self._reload_if_present()
            return self._enforced

    def list_keys(self) -> list[Dict[str, Any]]:
        with self._lock:
            self._reload_if_present()
            return [
                self._public_record(record)
                for record in sorted(
                    self._keys.values(),
                    key=lambda item: item["createdAt"],
                    reverse=True,
                )
            ]

    def create_key(
        self,
        *,
        name: str,
        scopes: Iterable[str],
        expires_at: Optional[str] = None,
    ) -> tuple[str, Dict[str, Any]]:
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 80 or "\n" in clean_name or "\r" in clean_name:
            raise ModelAPIKeyStoreError(
                "Model API key name must contain 1 to 80 characters."
            )
        clean_scopes = _clean_scopes(scopes)
        expiry = _parse_timestamp(expires_at, "expiresAt") if expires_at else None
        now = _utcnow()
        if expiry and expiry <= now:
            raise ModelAPIKeyStoreError("Model API key expiry must be in the future.")

        with self._lock:
            with self._process_lock():
                self._reload_if_present()
                key_id = secrets.token_hex(6)
                while key_id in self._keys:
                    key_id = secrets.token_hex(6)
                secret = secrets.token_urlsafe(32)
                prefix = f"{MODEL_API_KEY_PREFIX}{key_id}"
                token = f"{prefix}.{secret}"
                record = {
                    "id": key_id,
                    "name": clean_name,
                    "prefix": prefix,
                    "providerId": CODEX_OAUTH_PROVIDER_ID,
                    "secretHash": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                    "scopes": clean_scopes,
                    "createdAt": _timestamp(now),
                    "expiresAt": _timestamp(expiry) if expiry else None,
                    "lastUsedAt": None,
                    "revokedAt": None,
                }
                previous_enforced = self._enforced
                self._keys[key_id] = record
                self._enforced = True
                try:
                    self._save()
                except Exception:
                    self._keys.pop(key_id, None)
                    self._enforced = previous_enforced
                    raise
                return token, self._public_record(record)

    def revoke_key(self, key_id: str) -> Dict[str, Any]:
        normalized = str(key_id or "").strip().lower()
        with self._lock:
            with self._process_lock():
                self._reload_if_present()
                record = self._keys.get(normalized)
                if record is None:
                    raise KeyError(normalized)
                if record.get("revokedAt"):
                    return self._public_record(record)
                previous = deepcopy(record)
                record["revokedAt"] = _timestamp(_utcnow())
                try:
                    self._save()
                except Exception:
                    self._keys[normalized] = previous
                    raise
                return self._public_record(record)

    def authenticate(
        self,
        token: str,
        *,
        required_scope: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        candidate = str(token or "")
        if not candidate.startswith(MODEL_API_KEY_PREFIX) or "." not in candidate:
            return None
        prefix, secret = candidate.split(".", 1)
        key_id = prefix.removeprefix(MODEL_API_KEY_PREFIX)
        if not _KEY_ID_RE.fullmatch(key_id) or not secret:
            return None
        with self._lock:
            with self._process_lock():
                self._reload_if_present()
                record = self._keys.get(key_id)
                if record is None or self._status(record) != "active":
                    return None
                if required_scope and required_scope not in record["scopes"]:
                    return None
                supplied_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
                if not hmac.compare_digest(supplied_hash, record["secretHash"]):
                    return None
                now = _utcnow()
                last_used = _parse_timestamp(record.get("lastUsedAt"), "lastUsedAt")
                if (
                    last_used is None
                    or (now - last_used).total_seconds()
                    >= _LAST_USED_WRITE_INTERVAL_SECONDS
                ):
                    previous = record.get("lastUsedAt")
                    record["lastUsedAt"] = _timestamp(now)
                    try:
                        self._save()
                    except Exception:
                        record["lastUsedAt"] = previous
                        raise
                return {
                    "kind": "model_api_key",
                    "keyId": record["id"],
                    "providerId": record["providerId"],
                    "scopes": list(record["scopes"]),
                }


_stores_lock = threading.Lock()
_stores_by_path: Dict[str, ModelAPIKeyStore] = {}


def get_model_api_key_store(
    path: str | Path | None = None,
) -> ModelAPIKeyStore:
    selected = Path(
        path
        or os.getenv(
            "MCPO_MODEL_API_KEY_PATH",
            ".mcpo/model-api-keys.json",
        )
    )
    cache_key = str(selected.resolve())
    with _stores_lock:
        store = _stores_by_path.get(cache_key)
        if store is None:
            store = ModelAPIKeyStore(selected)
            _stores_by_path[cache_key] = store
        return store
