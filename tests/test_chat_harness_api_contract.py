"""Contract tests for the refreshed browser chat harness.

These tests intentionally describe API behavior that production code may not yet
implement.  The public shapes stay narrow:

* ``PATCH /chat/sessions/{id}`` updates only session chat settings.
* message attachments are base64 input objects with ``type``, ``name``,
  ``mime_type``, and ``data``.
* session responses retain attachment metadata, never the base64 ``data``.

Provider calls and model discovery are patched.  Tool-state tests use a temporary
``StateManager`` and in-memory mounted applications.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers.chat import get_session_manager, router
from mcpo.services.chat_sessions import ChatSessionManager
from mcpo.services.state import StateManager


MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024

# Smallest normalized capability shape needed by the chat endpoint. Provider
# catalogs may retain richer native metadata outside this field.
MODEL_CATALOG = [
    {
        "id": "shared-model",
        "label": "OpenRouter shared model",
        "provider": "openrouter",
        "capabilities": {"input_modalities": ["text"]},
    },
    {
        "id": "shared-model",
        "label": "OpenAI shared model",
        "provider": "openai",
        "capabilities": {"input_modalities": ["text"]},
    },
    {
        "id": "vision-model",
        "label": "Vision model",
        "provider": "openrouter",
        "capabilities": {"input_modalities": ["text", "image", "file"]},
    },
    {
        "id": "text-only-model",
        "label": "Text-only model",
        "provider": "openrouter",
        "capabilities": {"input_modalities": ["text"]},
    },
    {
        "id": "updated-model",
        "label": "Updated model",
        "provider": "openai",
        "capabilities": {"input_modalities": ["text", "image"]},
    },
]


def _provider_result() -> dict[str, Any]:
    return {
        "message": {"role": "assistant", "content": "Processed."},
        "tool_calls": [],
        "finish_reason": "stop",
        "clean_content": None,
    }


def _tool_definition(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Test tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _attachment(
    attachment_type: str,
    name: str,
    mime_type: str,
    payload: bytes,
) -> dict[str, str]:
    return {
        "type": attachment_type,
        "name": name,
        "mime_type": mime_type,
        "data": base64.b64encode(payload).decode("ascii"),
    }


def _assert_clear_422(response, *terms: str) -> None:
    assert response.status_code == 422, response.text
    body = json.dumps(response.json()).lower()
    for term in terms:
        assert term.lower() in body, response.text


def _session_app(manager: ChatSessionManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/chat")

    async def _manager_override() -> ChatSessionManager:
        return manager

    app.dependency_overrides[get_session_manager] = _manager_override
    return app


@dataclass
class ChatHarness:
    client: TestClient
    manager: ChatSessionManager
    provider_call: AsyncMock
    gather_tools: AsyncMock


@pytest.fixture()
def chat_harness() -> ChatHarness:
    manager = ChatSessionManager()
    app = _session_app(manager)
    provider_call = AsyncMock(return_value=_provider_result())
    gather_tools = AsyncMock(return_value=([], {}))

    with patch(
        "mcpo.api.routers.chat._load_model_catalog",
        new=AsyncMock(return_value=MODEL_CATALOG),
    ), patch(
        "mcpo.api.routers.chat._gather_tool_catalog",
        new=gather_tools,
    ), patch(
        "mcpo.api.routers.chat._get_client_for_model",
        return_value=MagicMock(),
    ), patch(
        "mcpo.api.routers.chat._call_provider",
        new=provider_call,
    ), TestClient(app) as client:
        yield ChatHarness(
            client=client,
            manager=manager,
            provider_call=provider_call,
            gather_tools=gather_tools,
        )


def _create_session(
    harness: ChatHarness,
    *,
    provider: str = "openrouter",
    model: str = "vision-model",
    **settings: Any,
) -> dict[str, Any]:
    body = {
        "provider": provider,
        "model": model,
        "skill_ids": [],
        **settings,
    }
    response = harness.client.post("/chat/sessions", json=body)
    assert response.status_code == 200, response.text
    return response.json()["session"]


def test_explicit_provider_is_stored_on_session(chat_harness: ChatHarness) -> None:
    session = _create_session(
        chat_harness,
        provider="openrouter",
        model="shared-model",
    )

    assert session.get("provider") == "openrouter"

    fetched = chat_harness.client.get(f"/chat/sessions/{session['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["session"].get("provider") == "openrouter"


def test_patch_session_updates_settings_without_recreating(
    chat_harness: ChatHarness,
) -> None:
    initial_tools = [_tool_definition("initial_tool")]
    refreshed_tools = [_tool_definition("refreshed_tool")]
    chat_harness.gather_tools.side_effect = [
        (initial_tools, {"initial_tool": {"server": "alpha"}}),
        (refreshed_tools, {"refreshed_tool": {"server": "beta"}}),
    ]

    created = _create_session(
        chat_harness,
        provider="openrouter",
        model="shared-model",
        system_prompt="Before patch.",
        server_allowlist=["alpha"],
        include_management_tools=False,
    )

    response = chat_harness.client.patch(
        f"/chat/sessions/{created['id']}",
        json={
            "provider": "openai",
            "model": "updated-model",
            "system_prompt": "After patch.",
            "server_allowlist": ["beta"],
            "skill_ids": [],
            "include_management_tools": True,
            "refresh_tools": True,
        },
    )

    assert response.status_code == 200, response.text
    updated = response.json()["session"]
    assert updated["id"] == created["id"]
    assert updated["createdAt"] == created["createdAt"]
    assert updated["provider"] == "openai"
    assert updated["model"] == "updated-model"
    assert updated["systemPrompt"] == "After patch."
    assert updated["serverAllowlist"] == ["beta"]
    assert updated["skillIds"] == []
    assert updated["includeManagementTools"] is True
    assert [tool["function"]["name"] for tool in updated["tools"]] == [
        "refreshed_tool"
    ]
    assert [
        message for message in updated["messages"] if message.get("role") == "system"
    ] == [{"role": "system", "content": "After patch."}]
    assert chat_harness.gather_tools.await_count == 2

    fetched = chat_harness.client.get(f"/chat/sessions/{created['id']}")
    assert fetched.status_code == 200
    fetched_session = fetched.json()["session"]
    assert fetched_session.pop("lastAccessed") >= updated.pop("lastAccessed")
    assert fetched_session == updated


def test_image_and_text_attachments_reach_chat_but_session_returns_metadata_only(
    chat_harness: ChatHarness,
) -> None:
    image_bytes = b"\x89PNG\r\n\x1a\ncontract-image-payload"
    text_bytes = b"private text attachment payload\nsecond line"
    image = _attachment("image", "pixel.png", "image/png", image_bytes)
    text = _attachment("text", "notes.txt", "text/plain", text_bytes)
    created = _create_session(chat_harness)

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={
            "message": "Inspect both attachments.",
            "stream": False,
            "attachments": [image, text],
        },
    )

    assert response.status_code == 200, response.text
    chat_harness.provider_call.assert_awaited_once()
    session = response.json()["session"]
    user_message = next(
        message for message in session["messages"] if message.get("role") == "user"
    )
    assert user_message["attachments"] == [
        {
            "type": "image",
            "name": "pixel.png",
            "mimeType": "image/png",
            "sizeBytes": len(image_bytes),
        },
        {
            "type": "text",
            "name": "notes.txt",
            "mimeType": "text/plain",
            "sizeBytes": len(text_bytes),
        },
    ]

    serialized_session = json.dumps(session)
    assert image["data"] not in serialized_session
    assert text["data"] not in serialized_session
    assert "data:image/png;base64" not in serialized_session


def test_attachment_count_is_limited_to_eight(chat_harness: ChatHarness) -> None:
    created = _create_session(chat_harness)
    attachments = [
        _attachment("text", f"note-{index}.txt", "text/plain", b"x")
        for index in range(MAX_ATTACHMENTS + 1)
    ]

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={"message": "Too many.", "stream": False, "attachments": attachments},
    )

    _assert_clear_422(response, "attachment", str(MAX_ATTACHMENTS))
    chat_harness.provider_call.assert_not_awaited()


def test_attachment_decoded_size_is_limited_to_five_mib(
    chat_harness: ChatHarness,
) -> None:
    created = _create_session(chat_harness)
    oversized = _attachment(
        "text",
        "oversized.txt",
        "text/plain",
        b"x" * (MAX_ATTACHMENT_BYTES + 1),
    )

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={"message": "Too large.", "stream": False, "attachments": [oversized]},
    )

    _assert_clear_422(response, "attachment", "5")
    chat_harness.provider_call.assert_not_awaited()


def test_attachment_total_decoded_size_is_limited_to_twenty_mib(
    chat_harness: ChatHarness,
) -> None:
    created = _create_session(chat_harness)
    part = b"x" * ((MAX_TOTAL_ATTACHMENT_BYTES // 5) + 1)
    attachments = [
        _attachment("text", f"part-{index}.txt", "text/plain", part)
        for index in range(5)
    ]
    assert all(len(part) <= MAX_ATTACHMENT_BYTES for _ in attachments)
    assert sum(len(part) for _ in attachments) > MAX_TOTAL_ATTACHMENT_BYTES

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={
            "message": "Too large together.",
            "stream": False,
            "attachments": attachments,
        },
    )

    _assert_clear_422(response, "attachment", "20")
    chat_harness.provider_call.assert_not_awaited()


@pytest.mark.parametrize(
    ("attachment_type", "name", "mime_type", "payload"),
    [
        ("image", "not-an-image.txt", "text/plain", b"plain text"),
        ("text", "program.bin", "application/octet-stream", b"binary"),
    ],
)
def test_attachment_type_and_mime_must_match_supported_inputs(
    chat_harness: ChatHarness,
    attachment_type: str,
    name: str,
    mime_type: str,
    payload: bytes,
) -> None:
    created = _create_session(chat_harness)
    invalid = _attachment(attachment_type, name, mime_type, payload)

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={"message": "Invalid MIME.", "stream": False, "attachments": [invalid]},
    )

    _assert_clear_422(response, "mime")
    chat_harness.provider_call.assert_not_awaited()


def test_unsupported_model_attachment_capability_returns_clear_422(
    chat_harness: ChatHarness,
) -> None:
    created = _create_session(chat_harness, model="text-only-model")
    image = _attachment(
        "image",
        "pixel.png",
        "image/png",
        b"\x89PNG\r\n\x1a\nvalid-enough-contract-payload",
    )

    response = chat_harness.client.post(
        f"/chat/sessions/{created['id']}/messages",
        json={
            "message": "This model cannot inspect images.",
            "stream": False,
            "attachments": [image],
        },
    )

    _assert_clear_422(response, "support", "image")
    chat_harness.provider_call.assert_not_awaited()


@dataclass
class ToolHarness:
    client: TestClient
    list_tools: AsyncMock


@pytest.fixture()
def tool_harness(tmp_path) -> ToolHarness:
    state = StateManager(str(tmp_path / "chat-tool-state.json"))
    state.set_tool_enabled("alpha", "disabled_tool", False)

    alpha_app = FastAPI()
    alpha_app.state.is_fastmcp_proxy = False
    alpha_app.state.is_connected = True
    list_tools = AsyncMock(
        return_value=SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="allowed_tool",
                    description="Enabled test tool",
                    input_schema={"type": "object", "properties": {}},
                ),
                SimpleNamespace(
                    name="disabled_tool",
                    description="Disabled test tool",
                    input_schema={"type": "object", "properties": {}},
                ),
            ]
        )
    )
    alpha_app.state.session = SimpleNamespace(list_tools=list_tools)

    management_app = FastAPI()

    @management_app.post("/reload", operation_id="mcpo_reload_config")
    async def _reload_config() -> dict[str, bool]:
        return {"ok": True}

    manager = ChatSessionManager()
    app = _session_app(manager)
    app.mount("/alpha", alpha_app)
    app.mount("/mcpo", management_app)

    with patch(
        "mcpo.api.routers.chat._load_model_catalog",
        new=AsyncMock(
            return_value=[
                {
                    "id": "tool-model",
                    "label": "Tool model",
                    "provider": "openrouter",
                    "capabilities": {
                        "input_modalities": ["text"],
                        "tool_calling": True,
                    },
                }
            ]
        ),
    ), patch(
        "mcpo.services.mcp_tools.get_state_manager",
        return_value=state,
    ), patch(
        "mcpo.services.state.get_state_manager",
        return_value=state,
    ), TestClient(app) as client:
        yield ToolHarness(client=client, list_tools=list_tools)


def _create_tool_session(
    harness: ToolHarness,
    *,
    include_management_tools: bool,
) -> dict[str, Any]:
    response = harness.client.post(
        "/chat/sessions",
        json={
            "provider": "openrouter",
            "model": "tool-model",
            "skill_ids": [],
            "server_allowlist": ["alpha"],
            "include_management_tools": include_management_tools,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["session"]


def _tool_names(session: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in session["tools"]}


def test_disabled_tools_are_excluded_from_chat_tool_catalog(
    tool_harness: ToolHarness,
) -> None:
    session = _create_tool_session(
        tool_harness,
        include_management_tools=True,
    )

    names = _tool_names(session)
    assert "alpha_allowed_tool" in names
    assert "alpha_disabled_tool" not in names
    tool_harness.list_tools.assert_awaited_once()


def test_explicit_allowlist_excludes_management_tools_until_opted_in(
    tool_harness: ToolHarness,
) -> None:
    excluded = _create_tool_session(
        tool_harness,
        include_management_tools=False,
    )
    assert excluded["serverAllowlist"] == ["alpha"]
    assert "mcpo_reload_config" not in _tool_names(excluded)

    included = _create_tool_session(
        tool_harness,
        include_management_tools=True,
    )
    assert included["serverAllowlist"] == ["alpha"]
    assert "mcpo_reload_config" in _tool_names(included)
