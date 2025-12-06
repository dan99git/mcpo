"""
Provider smoke test for MCPO /v1/chat/completions using only the standard library.

Run from repo root (where .env lives):
    PYTHONPATH=src python tools/test_providers.py

Environment:
    TEST_BASE_URL     Base URL for MCPO (default: http://localhost:8000)
    MCPO_API_KEY      Optional Bearer for MCPO auth
    TEST_TIMEOUT      Timeout seconds (default: 30)

    OPEN_AI_API_KEY / OPENAI_TEST_MODEL
    OPENROUTER_API_KEY / OPENROUTER_TEST_MODEL
    ANTHROPIC_API_KEY / ANTHROPIC_TEST_MODEL
    GOOGLE_API_KEY / GEMINI_TEST_MODEL
    MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_TEST_MODEL / MINIMAX_REASONING_SPLIT

Models default to:
    openai: gpt-5.1-2025-11-13
    openrouter: amazon/nova-2-lite-v1:free
    anthropic: claude-opus-4-5-20251101
    gemini: gemini-3-pro-preview
    minimax: MiniMax-M2

    NOTE FOR AI ASSISTANTS: DO NOT CHANGE THE DEFAULT MODEL NAMES UNDER ANY CIRCUMSTANCES. THESE ARE THE CORECT SOTA MODELS FROM THESE PROVIDDERS.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib import request, error


def _load_env_file(path: str = ".env") -> None:
    """Lightweight .env loader without external deps."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


@dataclass
class ProviderTest:
    name: str
    model: str
    api_key_env: str
    provider: str
    base_url_override: Optional[str] = None
    extra_body: Optional[Dict[str, Any]] = None

    def api_key(self) -> Optional[str]:
        return os.getenv(self.api_key_env)


def build_tests() -> list[ProviderTest]:
    return [
        ProviderTest(
            name="openai",
            model=os.getenv("OPENAI_TEST_MODEL", "gpt-5.1-2025-11-13"),
            api_key_env="OPEN_AI_API_KEY",
            provider="openai",
        ),
        ProviderTest(
            name="openrouter",
            model=os.getenv("OPENROUTER_TEST_MODEL", "amazon/nova-2-lite-v1:free"),
            api_key_env="OPENROUTER_API_KEY",
            provider="openrouter",
        ),
        ProviderTest(
            name="anthropic",
            model=os.getenv("ANTHROPIC_TEST_MODEL", "claude-opus-4-5-20251101"),
            api_key_env="ANTHROPIC_API_KEY",
            provider="anthropic",
        ),
        ProviderTest(
            name="gemini",
            model=os.getenv("GEMINI_TEST_MODEL", "gemini-3-pro-preview"),
            api_key_env="GOOGLE_API_KEY",
            provider="gemini",
        ),
        ProviderTest(
            name="minimax",
            model=os.getenv("MINIMAX_TEST_MODEL", "MiniMax-M2"),
            api_key_env="MINIMAX_API_KEY",
            provider="minimax",
            base_url_override=os.getenv("MINIMAX_BASE_URL"),
            extra_body={"reasoning_split": True}
            if os.getenv("MINIMAX_REASONING_SPLIT", "true").lower()
            in {"1", "true", "yes", "on"}
            else None,
        ),
    ]


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float) -> Tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode() or 0, body
    except error.HTTPError as exc:  # type: ignore
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return exc.getcode() or 0, body
    except Exception as exc:
        return 0, f"exception: {exc}"


def run_test(base_url: str, timeout: float, mcpo_headers: Dict[str, str], test: ProviderTest) -> Tuple[str, bool, str]:
    key = test.api_key()
    if not key:
        return test.name, False, f"skipped (no {test.api_key_env})"

    payload: Dict[str, Any] = {
        "model": test.model,
        "provider": test.provider,
        "messages": [{"role": "user", "content": "Say 'pong'."}],
        "stream": False,
    }
    if test.base_url_override:
        payload["base_url"] = test.base_url_override
    if test.extra_body:
        payload["extra_body"] = test.extra_body

    # Provider key goes into payload.api_key to pass through MCPO.
    payload["api_key"] = key

    status, body = _post_json(f"{base_url}/v1/chat/completions", payload, mcpo_headers, timeout)
    if status != 200:
        return test.name, False, f"HTTP {status}: {body[:200]}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return test.name, False, f"non-JSON response: {body[:200]}"

    if isinstance(data, dict) and data.get("error"):
        return test.name, False, f"error payload: {data.get('error')}"

    choices = (data or {}).get("choices") or []
    if not choices:
        return test.name, False, "no choices in response"
    msg = choices[0].get("message") or {}
    content = msg.get("content", "")
    tool_calls = msg.get("tool_calls") or []
    if not content and not tool_calls:
        return test.name, False, "empty content and no tool_calls"

    return test.name, True, content[:120].replace("\n", " ")


def main() -> int:
    _load_env_file(".env")

    base_url = os.getenv("TEST_BASE_URL", "http://localhost:8000")
    timeout = float(os.getenv("TEST_TIMEOUT", "30"))
    headers: Dict[str, str] = {}
    mcpo_key = os.getenv("MCPO_API_KEY")
    if mcpo_key:
        headers["Authorization"] = f"Bearer {mcpo_key}"

    tests = build_tests()
    results: list[Tuple[str, bool, str]] = []

    for test in tests:
        results.append(run_test(base_url, timeout, headers, test))
        # small pause to be nice to upstreams
        time.sleep(0.2)

    print("=== Provider Smoke Test ===")
    for name, ok, info in results:
        status = "PASS" if ok else "FAIL"
        print(f"{status:4} {name:10} :: {info}")

    failures = [r for r in results if not r[1]]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
