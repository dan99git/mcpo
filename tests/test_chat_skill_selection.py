from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers.chat import (
    ChatMessageRequest,
    CreateSessionRequest,
    _perform_exchange,
    create_session,
    router,
)
from mcpo.services.chat_sessions import ChatSession, ChatSessionManager
from mcpo.services.skills import SkillDefinition


SKILLS_MARKER = "Agent Skills (system-managed instructions):"
OLD_SKILL_PROMPT = (
    f"{SKILLS_MARKER}\n\n[Skill: Old skill | id=old]\nOld instructions."
)
NEW_SKILL_PROMPT = (
    f"{SKILLS_MARKER}\n\n[Skill: New skill | id=new]\nNew instructions."
)


def _skill(skill_id: str) -> SkillDefinition:
    return SkillDefinition(
        id=skill_id,
        title=f"{skill_id.title()} skill",
        description="",
        content=f"{skill_id.title()} instructions.",
    )


def _session_with_old_skill() -> ChatSession:
    system_prompt = f"Base prompt.\n\n{OLD_SKILL_PROMPT}"
    return ChatSession(
        id="session-1",
        model="test/model",
        system_prompt=system_prompt,
        messages=[{"role": "system", "content": system_prompt}],
        skill_ids=["old"],
    )


def _provider_result() -> dict:
    return {
        "message": {"role": "assistant", "content": "Done."},
        "tool_calls": [],
        "finish_reason": "stop",
        "clean_content": None,
    }


def test_set_favorites_is_blocked_in_read_only_mode() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/chat")
    app.state.read_only_mode = True
    state = MagicMock()

    with patch("mcpo.api.routers.chat.get_state_manager", return_value=state):
        response = TestClient(app).post(
            "/chat/sessions/favorites",
            json={"models": ["test/model"]},
        )

    assert response.status_code == 403
    assert response.json() == {
        "ok": False,
        "error": {
            "message": "Read-only mode enabled",
            "code": "read_only",
        },
    }
    state.set_favorite_models.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_data", [{}, {"skill_ids": None}], ids=["omitted", "null"])
async def test_create_session_uses_defaults_for_omitted_or_null_skill_ids(
    payload_data: dict,
) -> None:
    manager = ChatSessionManager()
    payload = CreateSessionRequest(
        model="test/model",
        system_prompt="Base prompt.",
        **payload_data,
    )
    default_skill = _skill("default")

    with patch(
        "mcpo.api.routers.chat._gather_tool_catalog",
        new_callable=AsyncMock,
        return_value=([], {}),
    ), patch(
        "mcpo.api.routers.chat._load_model_catalog",
        new_callable=AsyncMock,
        return_value=[{"id": "test/model", "label": "Test"}],
    ), patch(
        "mcpo.api.routers.chat.select_skills",
        return_value=[default_skill],
    ) as select_mock, patch(
        "mcpo.api.routers.chat.compile_skills_system_prompt",
        return_value=NEW_SKILL_PROMPT,
    ) as compile_mock:
        response = await create_session(MagicMock(), payload, manager)

    session = response.session
    assert session["skillIds"] == ["default"]
    assert NEW_SKILL_PROMPT in session["systemPrompt"]
    assert NEW_SKILL_PROMPT in session["messages"][0]["content"]
    select_mock.assert_called_once_with(
        scope="chat",
        model="test/model",
        provider=None,
        requested_skill_ids=None,
    )
    compile_mock.assert_called_once_with(
        scope="chat",
        model="test/model",
        provider=None,
        requested_skill_ids=None,
    )


@pytest.mark.asyncio
async def test_create_session_explicit_empty_skill_ids_selects_none() -> None:
    manager = ChatSessionManager()
    payload = CreateSessionRequest(
        model="test/model",
        system_prompt="Base prompt.",
        skill_ids=[],
    )

    with patch(
        "mcpo.api.routers.chat._gather_tool_catalog",
        new_callable=AsyncMock,
        return_value=([], {}),
    ), patch(
        "mcpo.api.routers.chat._load_model_catalog",
        new_callable=AsyncMock,
        return_value=[{"id": "test/model", "label": "Test"}],
    ), patch(
        "mcpo.api.routers.chat.select_skills",
    ) as select_mock, patch(
        "mcpo.api.routers.chat.compile_skills_system_prompt",
    ) as compile_mock:
        response = await create_session(MagicMock(), payload, manager)

    session = response.session
    assert session["skillIds"] == []
    assert session["systemPrompt"] == "Base prompt."
    assert session["messages"] == [{"role": "system", "content": "Base prompt."}]
    select_mock.assert_not_called()
    compile_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_data", [{}, {"skill_ids": None}], ids=["omitted", "null"])
async def test_message_omitted_or_null_skill_ids_preserves_selection(
    payload_data: dict,
) -> None:
    session = _session_with_old_skill()
    payload = ChatMessageRequest(message="Keep it.", stream=False, **payload_data)

    with patch(
        "mcpo.api.routers.chat.select_skills",
        return_value=[_skill("old")],
    ) as select_mock, patch(
        "mcpo.api.routers.chat.compile_skills_system_prompt",
        return_value=OLD_SKILL_PROMPT,
    ), patch(
        "mcpo.api.routers.chat._call_provider",
        new_callable=AsyncMock,
        return_value=_provider_result(),
    ):
        await _perform_exchange(
            session,
            MagicMock(),
            MagicMock(),
            payload,
            None,
            None,
            emitter=None,
        )

    assert session.skill_ids == ["old"]
    assert session.system_prompt == f"Base prompt.\n\n{OLD_SKILL_PROMPT}"
    assert session.messages[0]["content"] == session.system_prompt
    select_mock.assert_called_once_with(
        scope="chat",
        model="test/model",
        provider=None,
        requested_skill_ids=["old"],
    )


@pytest.mark.asyncio
async def test_message_explicit_empty_skill_ids_clears_managed_context() -> None:
    session = _session_with_old_skill()
    payload = ChatMessageRequest(message="Clear it.", stream=False, skill_ids=[])

    with patch("mcpo.api.routers.chat.select_skills") as select_mock, patch(
        "mcpo.api.routers.chat.compile_skills_system_prompt"
    ) as compile_mock, patch(
        "mcpo.api.routers.chat._call_provider",
        new_callable=AsyncMock,
        return_value=_provider_result(),
    ):
        await _perform_exchange(
            session,
            MagicMock(),
            MagicMock(),
            payload,
            None,
            None,
            emitter=None,
        )

    assert session.skill_ids == []
    assert session.system_prompt == "Base prompt."
    assert session.messages[0] == {"role": "system", "content": "Base prompt."}
    assert SKILLS_MARKER not in str(session.messages)
    select_mock.assert_not_called()
    compile_mock.assert_not_called()


@pytest.mark.asyncio
async def test_message_new_skill_ids_replace_stale_managed_context() -> None:
    session = _session_with_old_skill()
    payload = ChatMessageRequest(
        message="Replace it.",
        stream=False,
        skill_ids=["new"],
    )

    with patch(
        "mcpo.api.routers.chat.select_skills",
        return_value=[_skill("new")],
    ), patch(
        "mcpo.api.routers.chat.compile_skills_system_prompt",
        return_value=NEW_SKILL_PROMPT,
    ), patch(
        "mcpo.api.routers.chat._call_provider",
        new_callable=AsyncMock,
        return_value=_provider_result(),
    ):
        await _perform_exchange(
            session,
            MagicMock(),
            MagicMock(),
            payload,
            None,
            None,
            emitter=None,
        )

    assert session.skill_ids == ["new"]
    assert OLD_SKILL_PROMPT not in session.system_prompt
    assert OLD_SKILL_PROMPT not in str(session.messages)
    assert session.system_prompt == f"Base prompt.\n\n{NEW_SKILL_PROMPT}"
    assert session.messages[0]["content"] == session.system_prompt
