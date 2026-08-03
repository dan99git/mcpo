from __future__ import annotations

import base64
import json
import time

import pytest

from mcpo.api.routers.completions import (
    ChatMessage,
    CodexOAuthProvider,
    CompletionRequest,
    ToolDefinition,
    ToolFunction,
)
from mcpo.services import codex_oauth


def _jwt(payload):
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


@pytest.mark.asyncio
async def test_credentials_read_official_codex_auth_shape(tmp_path):
    path = tmp_path / "auth.json"
    access_token = _jwt({"exp": int(time.time()) + 3600})
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "id_token": _jwt({}),
                    "access_token": access_token,
                    "refresh_token": "refresh",
                    "account_id": "account",
                },
                "last_refresh": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    credentials = await codex_oauth.CodexOAuthCredentials(path).get()

    assert credentials == {
        "access_token": access_token,
        "account_id": "account",
    }


@pytest.mark.asyncio
async def test_expired_credentials_refresh_into_mcpo_sidecar(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": _jwt({}),
            "access_token": _jwt({"exp": int(time.time()) - 60}),
            "refresh_token": "old-refresh",
            "account_id": "account",
        },
        "last_refresh": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    fresh_access = _jwt({"exp": int(time.time()) + 3600})

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": fresh_access,
                "refresh_token": "new-refresh",
            }

    monkeypatch.setattr(codex_oauth.httpx, "post", lambda *args, **kwargs: FakeResponse())

    source = codex_oauth.CodexOAuthCredentials(path)
    credentials = await source.get()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    cached = json.loads(source.cache_path.read_text(encoding="utf-8"))
    restarted = await codex_oauth.CodexOAuthCredentials(path).get()

    assert credentials["access_token"] == fresh_access
    assert restarted["access_token"] == fresh_access
    assert persisted == original
    assert cached["tokens"]["refresh_token"] == "new-refresh"
    assert cached["last_refresh"] != "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_refresh_does_not_overwrite_a_concurrent_codex_login(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "auth.json"
    expired_access = _jwt({"exp": int(time.time()) - 60})
    concurrent_access = _jwt({"exp": int(time.time()) + 3600})
    original = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": _jwt({}),
            "access_token": expired_access,
            "refresh_token": "old-refresh",
            "account_id": "old-account",
        },
    }
    concurrent = {
        "auth_mode": "chatgpt",
        "concurrent_marker": True,
        "tokens": {
            "id_token": _jwt({}),
            "access_token": concurrent_access,
            "refresh_token": "concurrent-refresh",
            "account_id": "concurrent-account",
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": _jwt({"exp": int(time.time()) + 7200}),
                "refresh_token": "mcpo-refresh",
            }

    def fake_post(*args, **kwargs):
        path.write_text(json.dumps(concurrent), encoding="utf-8")
        return FakeResponse()

    monkeypatch.setattr(codex_oauth.httpx, "post", fake_post)

    credentials = await codex_oauth.CodexOAuthCredentials(path).get()
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert credentials == {
        "access_token": concurrent_access,
        "account_id": "concurrent-account",
    }
    assert persisted == concurrent


@pytest.mark.asyncio
async def test_sidecar_save_cannot_overwrite_a_late_codex_login(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": _jwt({}),
            "access_token": _jwt({"exp": int(time.time()) - 60}),
            "refresh_token": "old-refresh",
            "account_id": "old-account",
        },
    }
    concurrent_access = _jwt({"exp": int(time.time()) + 3600})
    concurrent = {
        "auth_mode": "chatgpt",
        "external_marker": True,
        "tokens": {
            "id_token": _jwt({}),
            "access_token": concurrent_access,
            "refresh_token": "concurrent-refresh",
            "account_id": "concurrent-account",
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    refreshed_access = _jwt({"exp": int(time.time()) + 7200})

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": refreshed_access,
                "refresh_token": "mcpo-refresh",
            }

    monkeypatch.setattr(codex_oauth.httpx, "post", lambda *args, **kwargs: FakeResponse())
    source = codex_oauth.CodexOAuthCredentials(path)
    original_save = source._save_cache

    def save_after_external_login(payload):
        path.write_text(json.dumps(concurrent), encoding="utf-8")
        original_save(payload)

    monkeypatch.setattr(source, "_save_cache", save_after_external_login)

    credentials = await source.get()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    next_credentials = await codex_oauth.CodexOAuthCredentials(path).get()

    assert credentials["access_token"] == refreshed_access
    assert persisted == concurrent
    assert next_credentials == {
        "access_token": concurrent_access,
        "account_id": "concurrent-account",
    }


@pytest.mark.asyncio
async def test_unrelated_codex_write_does_not_retry_rotating_refresh_token(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "auth.json"
    original = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": _jwt({}),
            "access_token": _jwt({"exp": int(time.time()) - 60}),
            "refresh_token": "old-refresh",
            "account_id": "account",
        },
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    fresh_access = _jwt({"exp": int(time.time()) + 3600})
    refresh_tokens = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": fresh_access,
                "refresh_token": "rotated-refresh",
            }

    def fake_post(*args, **kwargs):
        refresh_tokens.append(kwargs["data"]["refresh_token"])
        updated = dict(original)
        updated["external_marker"] = True
        path.write_text(json.dumps(updated), encoding="utf-8")
        return FakeResponse()

    monkeypatch.setattr(codex_oauth.httpx, "post", fake_post)

    credentials = await codex_oauth.CodexOAuthCredentials(path).get()
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert credentials["access_token"] == fresh_access
    assert refresh_tokens == ["old-refresh"]
    assert persisted["external_marker"] is True


def test_codex_request_translation_preserves_messages_and_tools():
    payload = CompletionRequest(
        model="gpt-test",
        messages=[
            ChatMessage(role="system", content="Be exact."),
            ChatMessage(role="user", content="Check it."),
            ChatMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "inspect",
                            "arguments": "{\"path\":\"a\"}",
                        },
                    }
                ],
            ),
            ChatMessage(
                role="tool",
                content="{\"ok\":true}",
                tool_call_id="call-1",
            ),
        ],
        tools=[
            ToolDefinition(
                function=ToolFunction(
                    name="inspect",
                    description="Inspect a path",
                    parameters={"type": "object"},
                )
            )
        ],
        tool_choice={
            "type": "function",
            "function": {"name": "inspect"},
        },
    )

    body, reverse_names = codex_oauth.build_codex_request(payload)

    assert body["stream"] is True
    assert body["model"] == "gpt-test"
    assert body["input"][0]["role"] == "developer"
    assert body["input"][1]["content"][0]["text"] == "Check it."
    assert body["input"][2]["type"] == "function_call"
    assert body["input"][3]["type"] == "function_call_output"
    assert body["tools"][0]["name"] == "inspect"
    assert body["tool_choice"] == {"type": "function", "name": "inspect"}
    assert reverse_names == {"inspect": "inspect"}


def test_codex_terminal_response_translation():
    event = {
        "type": "response.completed",
        "response": {
            "id": "resp-1",
            "created_at": 123,
            "model": "gpt-test",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Checked."}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                },
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "inspect",
                    "arguments": "{}",
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        },
    }

    result = codex_oauth.codex_response_to_chat(
        event,
        requested_model="gpt-test",
        reverse_names={"inspect": "inspect_original"},
    )

    message = result["choices"][0]["message"]
    assert message["content"] == "Done."
    assert message["reasoning_content"] == "Checked."
    assert message["tool_calls"][0]["function"]["name"] == "inspect_original"
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    assert result["usage"]["total_tokens"] == 15


def test_codex_stream_tool_deltas_keep_the_announced_index():
    state = codex_oauth.CodexStreamState(
        requested_model="gpt-test",
        reverse_names={},
    )
    announced = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.output_item.added",
            "item": {
                "id": "item-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "inspect",
            },
        },
        state,
    )
    delta = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item-1",
            "delta": "{}",
        },
        state,
    )

    assert announced[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0
    assert delta[0]["choices"][0]["delta"]["tool_calls"][0]["index"] == 0


def test_codex_stream_uses_done_arguments_when_deltas_are_missing():
    state = codex_oauth.CodexStreamState(
        requested_model="gpt-test",
        reverse_names={},
    )
    codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.output_item.added",
            "item": {
                "id": "item-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "inspect",
            },
        },
        state,
    )

    chunks = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.function_call_arguments.done",
            "item_id": "item-1",
            "arguments": "{\"path\":\"a\"}",
        },
        state,
    )

    tool = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tool["index"] == 0
    assert tool["function"]["arguments"] == "{\"path\":\"a\"}"


def test_codex_stream_uses_output_item_done_as_tool_fallback():
    state = codex_oauth.CodexStreamState(
        requested_model="gpt-test",
        reverse_names={"inspect": "inspect_original"},
    )

    chunks = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.output_item.done",
            "item": {
                "id": "item-1",
                "type": "function_call",
                "call_id": "call-1",
                "name": "inspect",
                "arguments": "{}",
            },
        },
        state,
    )

    tool = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tool["id"] == "call-1"
    assert tool["function"] == {
        "name": "inspect_original",
        "arguments": "{}",
    }


def test_codex_stream_emits_images_and_deduplicates_final_item():
    state = codex_oauth.CodexStreamState(
        requested_model="gpt-test",
        reverse_names={},
    )
    partial = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.image_generation_call.partial_image",
            "item_id": "image-1",
            "output_format": "png",
            "partial_image_b64": "aGVsbG8=",
        },
        state,
    )
    duplicate = codex_oauth.codex_event_to_chat_chunks(
        {
            "type": "response.output_item.done",
            "item": {
                "id": "image-1",
                "type": "image_generation_call",
                "output_format": "png",
                "result": "aGVsbG8=",
            },
        },
        state,
    )

    image = partial[0]["choices"][0]["delta"]["images"][0]
    assert image["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    assert duplicate == []


@pytest.mark.asyncio
async def test_codex_provider_rebuilds_terminal_output_from_sse(monkeypatch):
    provider = CodexOAuthProvider()

    async def fake_events(body):
        yield {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "message",
                "content": [{"type": "output_text", "text": "OK"}],
            },
        }
        yield {
            "type": "response.completed",
            "response": {
                "id": "resp-1",
                "created_at": 123,
                "model": body["model"],
                "status": "completed",
                "output": [],
            },
        }

    monkeypatch.setattr(provider, "_events", fake_events)
    result = await provider.complete(
        CompletionRequest(
            model="gpt-test",
            messages=[ChatMessage(role="user", content="Say OK")],
        )
    )

    assert result["choices"][0]["message"]["content"] == "OK"


def test_codex_model_cache_is_normalized(tmp_path):
    path = tmp_path / "models_cache.json"
    path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-test",
                        "display_name": "GPT Test",
                        "description": "Test model",
                        "supported_in_api": True,
                        "input_modalities": ["text", "image"],
                        "context_window": 100000,
                    },
                    {
                        "slug": "hidden",
                        "supported_in_api": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    models = codex_oauth.load_codex_models(path)

    assert [model["id"] for model in models] == ["gpt-test"]
    assert models[0]["inputModalities"] == ["text", "image"]
    assert models[0]["contextWindow"] == 100000
