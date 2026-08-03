"""In-memory per-key usage accounting for the model API.

Answers "which client is burning my quota" at request granularity. Deliberately
NOT persisted and NOT on the key-store file path: the adversarial audit proved
that synchronous file-locked writes in the auth hot path freeze the event loop,
so this recorder is process-local memory only. Counters reset on restart and are
per worker process — both stated in the API response via "sinceStartup": true.

Token-level accounting is intentionally absent: the middleware cannot read
response bodies without buffering streams. If token counts are wanted later they
must be recorded by the completions provider code, not here.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

_STARTED_AT = time.time()


class UsageRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: Dict[str, Dict[str, Any]] = {}

    def record(self, key_id: str, *, path: str, status_code: int) -> None:
        identity = str(key_id or "")
        if not identity:
            return
        now = time.time()
        with self._lock:
            entry = self._by_key.get(identity)
            if entry is None:
                entry = {
                    "requests": 0,
                    "errors": 0,
                    "rateLimited": 0,
                    "byPath": {},
                    "lastStatus": None,
                    "lastPath": None,
                    "lastAt": None,
                }
                self._by_key[identity] = entry
            entry["requests"] += 1
            if status_code >= 400:
                entry["errors"] += 1
            if status_code == 429:
                entry["rateLimited"] += 1
            per_path = entry["byPath"]
            per_path[path] = per_path.get(path, 0) + 1
            entry["lastStatus"] = status_code
            entry["lastPath"] = path
            entry["lastAt"] = now

    def snapshot(self, key_id: str | None = None) -> Dict[str, Any]:
        """Copy of the counters: one key's entry, or all keys keyed by id."""
        with self._lock:
            if key_id is not None:
                entry = self._by_key.get(str(key_id))
                return _public(entry) if entry else _empty()
            return {k: _public(v) for k, v in self._by_key.items()}

    def reset(self) -> None:
        with self._lock:
            self._by_key.clear()


def _empty() -> Dict[str, Any]:
    return {
        "requests": 0,
        "errors": 0,
        "rateLimited": 0,
        "byPath": {},
        "lastStatus": None,
        "lastPath": None,
        "lastAt": None,
        "sinceStartup": True,
    }


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "requests": entry["requests"],
        "errors": entry["errors"],
        "rateLimited": entry["rateLimited"],
        "byPath": dict(entry["byPath"]),
        "lastStatus": entry["lastStatus"],
        "lastPath": entry["lastPath"],
        "lastAt": entry["lastAt"],
        "sinceStartup": True,
    }


_recorder_lock = threading.Lock()
_recorder: UsageRecorder | None = None


def get_usage_recorder() -> UsageRecorder:
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = UsageRecorder()
        return _recorder
