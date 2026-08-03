from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import httpx


CODEX_AUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_BACKEND_URL = "https://chatgpt.com/backend-api/codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = "codex_cli_rs/0.145.0"
_REFRESH_LEAD_SECONDS = 300


class CodexOAuthError(RuntimeError):
    """Raised when local Codex OAuth credentials or upstream responses are invalid."""


_path_locks_guard = threading.Lock()
_path_locks: Dict[str, threading.RLock] = {}


def codex_home() -> Path:
    configured = os.getenv("CODEX_HOME")
    return Path(configured) if configured else Path.home() / ".codex"


def codex_auth_path() -> Path:
    configured = os.getenv("MCPO_CODEX_AUTH_PATH")
    return Path(configured) if configured else codex_home() / "auth.json"


def codex_models_path() -> Path:
    configured = os.getenv("MCPO_CODEX_MODELS_PATH")
    return Path(configured) if configured else codex_home() / "models_cache.json"


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _path_locks[key] = lock
        return lock


def _token_cache_path(auth_path: Path) -> Path:
    configured = os.getenv("MCPO_CODEX_TOKEN_CACHE_PATH")
    return (
        Path(configured)
        if configured
        else auth_path.with_name("mcpo-oauth-cache.json")
    )


def _token_fingerprint(tokens: Dict[str, Any]) -> str:
    material = {
        key: str(tokens.get(key) or "")
        for key in ("access_token", "refresh_token", "id_token", "account_id")
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            acquired = True
        except OSError as exc:
            raise CodexOAuthError(
                f"Could not lock the MCPO Codex token cache: {exc}"
            ) from exc
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _read_object_with_revision(path: Path, label: str) -> tuple[Dict[str, Any], str]:
    if not path.exists():
        raise CodexOAuthError(f"{label} was not found at {path}. Run 'codex login'.")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CodexOAuthError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CodexOAuthError(f"{label} must contain a JSON object.")
    return payload, hashlib.sha256(raw).hexdigest()


def _read_object(path: Path, label: str) -> Dict[str, Any]:
    payload, _ = _read_object_with_revision(path, label)
    return payload


def _jwt_claims(token: str) -> Dict[str, Any]:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _jwt_expiry(token: str) -> Optional[int]:
    value = _jwt_claims(token).get("exp")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _account_id_from_id_token(token: str) -> str:
    claims = _jwt_claims(token)
    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        value = auth_claims.get("chatgpt_account_id")
        if value:
            return str(value)
    return ""


def codex_oauth_status(path: Optional[Path] = None) -> Dict[str, Any]:
    selected = path or codex_auth_path()
    try:
        payload = _read_object(selected, "Codex auth file")
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            raise CodexOAuthError("Codex auth file does not contain a tokens object.")
        if not str(tokens.get("access_token") or "").strip():
            raise CodexOAuthError("Codex auth file does not contain an access token.")
        account_id = str(tokens.get("account_id") or "").strip()
        if not account_id:
            account_id = _account_id_from_id_token(
                str(tokens.get("id_token") or "")
            )
        if not account_id:
            raise CodexOAuthError("Codex auth file does not contain an account id.")
        return {"ready": True, "source": "codex-cli"}
    except CodexOAuthError as exc:
        return {"ready": False, "source": "codex-cli", "detail": str(exc)}


class CodexOAuthCredentials:
    def __init__(
        self,
        path: Optional[Path] = None,
        cache_path: Optional[Path] = None,
    ) -> None:
        self.path = path or codex_auth_path()
        self.cache_path = cache_path or _token_cache_path(self.path)
        self._cache_lock_path = self.cache_path.with_name(
            f".{self.cache_path.name}.lock"
        )
        self._lock = _path_lock(self.cache_path)

    async def get(self, *, force_refresh: bool = False) -> Dict[str, str]:
        return await asyncio.to_thread(self._get_sync, force_refresh)

    def _get_sync(self, force_refresh: bool) -> Dict[str, str]:
        with self._lock:
            with _exclusive_file_lock(self._cache_lock_path):
                refresh_requested = force_refresh
                conflicted_access_token: Optional[str] = None
                for _ in range(3):
                    payload = _read_object(self.path, "Codex auth file")
                    source_tokens = payload.get("tokens")
                    if not isinstance(source_tokens, dict):
                        raise CodexOAuthError(
                            "Codex auth file does not contain a tokens object."
                        )
                    source_fingerprint = _token_fingerprint(source_tokens)
                    cached = self._load_cache(source_fingerprint)
                    tokens = dict(
                        cached.get("tokens")
                        if cached is not None
                        else source_tokens
                    )
                    access_token = str(tokens.get("access_token") or "").strip()
                    refresh_token = str(tokens.get("refresh_token") or "").strip()
                    account_id = str(tokens.get("account_id") or "").strip()
                    if not access_token:
                        raise CodexOAuthError(
                            "Codex auth file does not contain an access token."
                        )

                    if conflicted_access_token is not None:
                        refresh_requested = access_token == conflicted_access_token
                        conflicted_access_token = None
                    expires_at = _jwt_expiry(access_token)
                    needs_refresh = refresh_requested or (
                        expires_at is not None
                        and expires_at <= int(time.time()) + _REFRESH_LEAD_SECONDS
                    )
                    if needs_refresh:
                        if not refresh_token:
                            raise CodexOAuthError(
                                "Codex access token expired and no refresh token is available. "
                                "Run 'codex login'."
                            )
                        refreshed = self._refresh(
                            tokens,
                            refresh_token,
                            source_fingerprint=source_fingerprint,
                        )
                        if refreshed is None:
                            conflicted_access_token = access_token
                            continue
                        tokens = refreshed
                        access_token = str(
                            tokens.get("access_token") or ""
                        ).strip()
                        account_id = str(tokens.get("account_id") or "").strip()

                    if not account_id:
                        account_id = _account_id_from_id_token(
                            str(tokens.get("id_token") or "")
                        )
                    if not access_token or not account_id:
                        raise CodexOAuthError(
                            "Codex OAuth credentials are missing the access token or account id."
                        )
                    return {
                        "access_token": access_token,
                        "account_id": account_id,
                    }
                raise CodexOAuthError(
                    "Codex auth changed repeatedly during token refresh. Retry the request."
                )

    def _load_cache(self, source_fingerprint: str) -> Optional[Dict[str, Any]]:
        if not self.cache_path.exists():
            return None
        payload = _read_object(self.cache_path, "MCPO Codex token cache")
        if payload.get("version") != 1:
            raise CodexOAuthError("MCPO Codex token cache version is invalid.")
        if payload.get("sourceFingerprint") != source_fingerprint:
            return None
        if not isinstance(payload.get("tokens"), dict):
            raise CodexOAuthError("MCPO Codex token cache has no tokens object.")
        return payload

    def _refresh(
        self,
        tokens: Dict[str, Any],
        refresh_token: str,
        *,
        source_fingerprint: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            response = httpx.post(
                CODEX_AUTH_TOKEN_URL,
                data={
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": "openid profile email",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise CodexOAuthError(
                f"Codex OAuth token refresh failed: {type(exc).__name__}."
            ) from exc
        if response.status_code != 200:
            raise CodexOAuthError(
                f"Codex OAuth token refresh failed with HTTP {response.status_code}. "
                "Run 'codex login' if the refresh token was revoked."
            )
        try:
            refreshed = response.json()
        except ValueError as exc:
            raise CodexOAuthError(
                "Codex OAuth token refresh returned invalid JSON."
            ) from exc
        if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
            raise CodexOAuthError(
                "Codex OAuth token refresh did not return an access token."
            )

        updated_tokens = dict(tokens)
        for key in ("access_token", "refresh_token", "id_token"):
            value = refreshed.get(key)
            if value:
                updated_tokens[key] = value
        account_id = _account_id_from_id_token(
            str(updated_tokens.get("id_token") or "")
        )
        if account_id:
            updated_tokens["account_id"] = account_id
        current_payload = _read_object(self.path, "Codex auth file")
        current_tokens = current_payload.get("tokens")
        if not isinstance(current_tokens, dict):
            raise CodexOAuthError(
                "Codex auth file does not contain a tokens object."
            )
        if _token_fingerprint(current_tokens) != source_fingerprint:
            return None
        self._save_cache(
            {
                "version": 1,
                "sourceFingerprint": source_fingerprint,
                "tokens": updated_tokens,
                "last_refresh": datetime.now(timezone.utc).isoformat(),
            }
        )
        return updated_tokens

    def _save_cache(self, payload: Dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.cache_path.name}.",
            suffix=".tmp",
            dir=str(self.cache_path.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.cache_path)
            try:
                os.chmod(self.cache_path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise CodexOAuthError(
                f"Failed to save the MCPO Codex token cache: {exc}"
            ) from exc


def load_codex_models(path: Optional[Path] = None) -> list[Dict[str, Any]]:
    selected = path or codex_models_path()
    try:
        payload = _read_object(selected, "Codex model cache")
    except CodexOAuthError:
        return []
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []

    models: list[Dict[str, Any]] = []
    for raw in raw_models:
        if not isinstance(raw, dict) or raw.get("supported_in_api") is False:
            continue
        model_id = str(raw.get("slug") or "").strip()
        if not model_id:
            continue
        modalities = raw.get("input_modalities")
        if not isinstance(modalities, list):
            modalities = ["text"]
        entry: Dict[str, Any] = {
            "id": model_id,
            "label": str(raw.get("display_name") or model_id),
            "description": str(raw.get("description") or ""),
            "inputModalities": modalities,
            "capabilities": ["text", "tools", "reasoning"],
        }
        if any(str(item).lower() == "image" for item in modalities):
            entry["capabilities"].append("images")
        context_window = raw.get("context_window")
        if context_window:
            entry["contextWindow"] = context_window
        models.append(entry)
    return models


def _short_tool_name(name: str) -> str:
    if len(name) <= 64:
        return name
    if name.startswith("mcp__") and "__" in name[5:]:
        candidate = "mcp__" + name.rsplit("__", 1)[1]
        return candidate[:64]
    return name[:64]


def _tool_name_maps(names: Iterable[str]) -> tuple[Dict[str, str], Dict[str, str]]:
    forward: Dict[str, str] = {}
    reverse: Dict[str, str] = {}
    for original in names:
        base = _short_tool_name(original)
        candidate = base
        suffix = 1
        while candidate in reverse and reverse[candidate] != original:
            marker = f"_{suffix}"
            candidate = base[: 64 - len(marker)] + marker
            suffix += 1
        forward[original] = candidate
        reverse[candidate] = original
    return forward, reverse


def _message_part(role: str, content: Any) -> list[Dict[str, Any]]:
    output: list[Dict[str, Any]] = []
    text_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        if content:
            output.append({"type": text_type, "text": content})
        return output
    if not isinstance(content, list):
        return output
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            output.append({"type": text_type, "text": str(item.get("text") or "")})
        elif item_type == "image_url" and role == "user":
            image = item.get("image_url")
            image_url = image.get("url") if isinstance(image, dict) else None
            if image_url:
                output.append({"type": "input_image", "image_url": image_url})
        elif item_type == "file" and role == "user":
            raw_file = item.get("file")
            if not isinstance(raw_file, dict) or not raw_file.get("file_data"):
                continue
            part = {"type": "input_file", "file_data": raw_file["file_data"]}
            if raw_file.get("filename"):
                part["filename"] = raw_file["filename"]
            output.append(part)
    return output


def build_codex_request(payload: Any) -> tuple[Dict[str, Any], Dict[str, str]]:
    raw_tools = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in (payload.tools or [])
    ]
    tool_names = [
        str(tool.get("function", {}).get("name") or "")
        for tool in raw_tools
        if tool.get("type") == "function"
    ]
    forward_names, reverse_names = _tool_name_maps(
        name for name in tool_names if name
    )

    input_items: list[Dict[str, Any]] = []
    pending_calls: Dict[str, str] = {}
    for message in payload.messages:
        role = str(message.role or "")
        if role == "tool":
            call_id = str(message.tool_call_id or "")
            if call_id and call_id in pending_calls:
                pending_calls.pop(call_id, None)
                output = message.content
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    }
                )
            continue

        content = _message_part(role, message.content)
        mapped_role = "developer" if role == "system" else role
        if role != "assistant" or content:
            input_items.append(
                {
                    "type": "message",
                    "role": mapped_role,
                    "content": content,
                }
            )
        if role == "assistant":
            for index, tool_call in enumerate(message.tool_calls or []):
                if not isinstance(tool_call, dict) or tool_call.get("type") != "function":
                    continue
                call_id = str(tool_call.get("id") or f"call_missing_{index}")
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                name = str(function.get("name") or "")
                pending_calls[call_id] = call_id
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": forward_names.get(name, _short_tool_name(name)),
                        "arguments": str(function.get("arguments") or ""),
                    }
                )

    body: Dict[str, Any] = {
        "instructions": "",
        "stream": True,
        "model": payload.model,
        "input": input_items,
        "parallel_tool_calls": True,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
    }
    extra_body = payload.extra_body if isinstance(payload.extra_body, dict) else {}
    reasoning = extra_body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        body["reasoning"]["effort"] = reasoning["effort"]
    elif extra_body.get("reasoning_effort"):
        body["reasoning"]["effort"] = extra_body["reasoning_effort"]

    if raw_tools:
        body["tools"] = []
        for tool in raw_tools:
            if tool.get("type") != "function":
                body["tools"].append(tool)
                continue
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            mapped = {
                "type": "function",
                "name": forward_names.get(
                    str(function.get("name") or ""),
                    _short_tool_name(str(function.get("name") or "")),
                ),
                "description": function.get("description"),
                "parameters": function.get("parameters") or {},
            }
            body["tools"].append(
                {key: value for key, value in mapped.items() if value is not None}
            )
    if payload.tool_choice is not None:
        choice = payload.tool_choice
        if isinstance(choice, dict) and choice.get("type") == "function":
            function = choice.get("function")
            name = function.get("name") if isinstance(function, dict) else ""
            body["tool_choice"] = {
                "type": "function",
                "name": forward_names.get(str(name), _short_tool_name(str(name))),
            }
        else:
            body["tool_choice"] = choice
    if payload.response_format:
        response_format = payload.response_format
        if response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema") or {}
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema.get("name"),
                    "strict": schema.get("strict"),
                    "schema": schema.get("schema"),
                }
            }
        elif response_format.get("type") == "text":
            body["text"] = {"format": {"type": "text"}}
    return body, reverse_names


def codex_response_to_chat(
    event: Dict[str, Any],
    *,
    requested_model: str,
    reverse_names: Dict[str, str],
) -> Dict[str, Any]:
    response = event.get("response")
    if not isinstance(response, dict):
        raise CodexOAuthError("Codex response did not contain a response object.")
    content = ""
    reasoning = ""
    tool_calls: list[Dict[str, Any]] = []
    images: list[Dict[str, Any]] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    content += str(part.get("text") or "")
        elif item_type == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict) and part.get("type") == "summary_text":
                    reasoning += str(part.get("text") or "")
        elif item_type == "function_call":
            name = str(item.get("name") or "")
            tool_calls.append(
                {
                    "id": str(item.get("call_id") or ""),
                    "type": "function",
                    "function": {
                        "name": reverse_names.get(name, name),
                        "arguments": str(item.get("arguments") or ""),
                    },
                }
            )
        elif item_type == "image_generation_call" and item.get("result"):
            output_format = str(item.get("output_format") or "png").lower()
            mime_type = (
                "image/jpeg"
                if output_format in {"jpg", "jpeg"}
                else f"image/{output_format}"
            )
            images.append(
                {
                    "index": len(images),
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{item['result']}"
                    },
                }
            )
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls
    if images:
        message["images"] = images

    status_value = str(response.get("status") or "")
    native_finish_reason = "stop"
    finish_reason = "tool_calls" if tool_calls else "stop"
    if status_value == "incomplete":
        details = response.get("incomplete_details")
        native_finish_reason = (
            str(details.get("reason") or "") if isinstance(details, dict) else ""
        )
        finish_reason = {
            "max_tokens": "length",
            "max_output_tokens": "length",
            "content_filter": "content_filter",
        }.get(native_finish_reason, "stop")
    choice = {
        "index": 0,
        "message": message,
        "finish_reason": finish_reason,
        "native_finish_reason": native_finish_reason,
    }
    result: Dict[str, Any] = {
        "id": str(response.get("id") or ""),
        "object": "chat.completion",
        "created": int(response.get("created_at") or time.time()),
        "model": str(response.get("model") or requested_model),
        "choices": [choice],
    }
    usage = response.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }
    return result


@dataclass
class CodexStreamState:
    requested_model: str
    reverse_names: Dict[str, str]
    response_id: str = ""
    model: str = ""
    created: int = 0
    tool_indexes: Dict[str, int] = field(default_factory=dict)
    tool_arguments_seen: set[str] = field(default_factory=set)
    image_hashes: Dict[str, str] = field(default_factory=dict)
    text_seen: bool = False


def codex_event_to_chat_chunks(
    event: Dict[str, Any],
    state: CodexStreamState,
) -> list[Dict[str, Any]]:
    event_type = str(event.get("type") or "")
    response = event.get("response")
    if event_type == "response.created" and isinstance(response, dict):
        state.response_id = str(response.get("id") or "")
        state.model = str(response.get("model") or state.requested_model)
        state.created = int(response.get("created_at") or time.time())
        return []

    delta: Dict[str, Any] = {}
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    if event_type == "response.output_text.delta":
        state.text_seen = True
        delta = {"role": "assistant", "content": str(event.get("delta") or "")}
    elif event_type == "response.reasoning_summary_text.delta":
        delta = {
            "role": "assistant",
            "reasoning_content": str(event.get("delta") or ""),
        }
    elif event_type == "response.output_item.added":
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return []
        call_id = str(item.get("call_id") or "")
        index = max(state.tool_indexes.values(), default=-1) + 1
        for identity in (call_id, str(item.get("id") or "")):
            if identity:
                state.tool_indexes[identity] = index
        name = str(item.get("name") or "")
        delta = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": state.reverse_names.get(name, name),
                        "arguments": "",
                    },
                }
            ],
        }
    elif event_type == "response.function_call_arguments.delta":
        call_id = str(event.get("item_id") or event.get("call_id") or "")
        if call_id:
            state.tool_arguments_seen.add(call_id)
        index = state.tool_indexes.get(
            call_id, max(state.tool_indexes.values(), default=0)
        )
        delta = {
            "tool_calls": [
                {
                    "index": index,
                    "function": {"arguments": str(event.get("delta") or "")},
                }
            ]
        }
    elif event_type == "response.function_call_arguments.done":
        call_id = str(event.get("item_id") or event.get("call_id") or "")
        if call_id in state.tool_arguments_seen:
            return []
        if call_id:
            state.tool_arguments_seen.add(call_id)
        index = state.tool_indexes.get(
            call_id, max(state.tool_indexes.values(), default=0)
        )
        delta = {
            "tool_calls": [
                {
                    "index": index,
                    "function": {"arguments": str(event.get("arguments") or "")},
                }
            ]
        }
    elif event_type == "response.image_generation_call.partial_image":
        item_id = str(event.get("item_id") or "")
        encoded = str(event.get("partial_image_b64") or "")
        if not encoded:
            return []
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if item_id and state.image_hashes.get(item_id) == digest:
            return []
        if item_id:
            state.image_hashes[item_id] = digest
        output_format = str(event.get("output_format") or "png").lower()
        mime_type = (
            "image/jpeg"
            if output_format in {"jpg", "jpeg"}
            else f"image/{output_format}"
        )
        delta = {
            "role": "assistant",
            "images": [
                {
                    "index": 0,
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}"
                    },
                }
            ],
        }
    elif event_type == "response.output_item.done":
        item = event.get("item")
        if not isinstance(item, dict):
            return []
        item_type = str(item.get("type") or "")
        if item_type == "image_generation_call":
            item_id = str(item.get("id") or "")
            encoded = str(item.get("result") or "")
            if not encoded:
                return []
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if item_id and state.image_hashes.get(item_id) == digest:
                return []
            if item_id:
                state.image_hashes[item_id] = digest
            output_format = str(item.get("output_format") or "png").lower()
            mime_type = (
                "image/jpeg"
                if output_format in {"jpg", "jpeg"}
                else f"image/{output_format}"
            )
            delta = {
                "role": "assistant",
                "images": [
                    {
                        "index": 0,
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{encoded}"
                        },
                    }
                ],
            }
        elif item_type == "function_call":
            call_id = str(item.get("call_id") or "")
            item_id = str(item.get("id") or "")
            identities = [value for value in (call_id, item_id) if value]
            known_indexes = [
                state.tool_indexes[value]
                for value in identities
                if value in state.tool_indexes
            ]
            arguments_seen = any(
                value in state.tool_arguments_seen for value in identities
            )
            if known_indexes:
                if arguments_seen:
                    return []
                index = known_indexes[0]
                state.tool_arguments_seen.update(identities)
                delta = {
                    "tool_calls": [
                        {
                            "index": index,
                            "function": {
                                "arguments": str(item.get("arguments") or "")
                            },
                        }
                    ]
                }
            else:
                index = max(state.tool_indexes.values(), default=-1) + 1
                for identity in identities:
                    state.tool_indexes[identity] = index
                    state.tool_arguments_seen.add(identity)
                name = str(item.get("name") or "")
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": state.reverse_names.get(name, name),
                                "arguments": str(item.get("arguments") or ""),
                            },
                        }
                    ],
                }
        elif item_type == "message" and not state.text_seen:
            content = "".join(
                str(part.get("text") or "")
                for part in item.get("content") or []
                if isinstance(part, dict) and part.get("type") == "output_text"
            )
            if not content:
                return []
            state.text_seen = True
            delta = {"role": "assistant", "content": content}
        else:
            return []
    elif event_type in {"response.completed", "response.incomplete"}:
        response = response if isinstance(response, dict) else {}
        finish_reason = "stop"
        if state.tool_indexes:
            finish_reason = "tool_calls"
        if event_type == "response.incomplete":
            details = response.get("incomplete_details")
            reason = (
                str(details.get("reason") or "")
                if isinstance(details, dict)
                else ""
            )
            finish_reason = {
                "max_tokens": "length",
                "max_output_tokens": "length",
                "content_filter": "content_filter",
            }.get(reason, "stop")
        raw_usage = response.get("usage")
        if isinstance(raw_usage, dict):
            usage = {
                "prompt_tokens": int(raw_usage.get("input_tokens") or 0),
                "completion_tokens": int(raw_usage.get("output_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            }
    else:
        return []

    chunk: Dict[str, Any] = {
        "id": state.response_id,
        "object": "chat.completion.chunk",
        "created": state.created or int(time.time()),
        "model": state.model or state.requested_model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        chunk["usage"] = usage
    return [chunk]
