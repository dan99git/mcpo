from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcpo.api.routers.chat import ChatMessageRequest, _perform_exchange
from mcpo.services.chat_sessions import ChatSession


@pytest.mark.asyncio
async def test_chat_exchange_stops_after_maximum_tool_rounds() -> None:
    session = ChatSession(id="session-1", model="test/model", system_prompt=None)
    payload = ChatMessageRequest(
        message="Keep calling tools.",
        stream=False,
        max_tool_rounds=2,
    )
    call_number = 0

    async def endless_tool_calls(*args, **kwargs):
        nonlocal call_number
        call_number += 1
        tool_call = {
            "id": f"call-{call_number}",
            "type": "function",
            "function": {"name": "demo", "arguments": "{}"},
        }
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [tool_call],
            },
            "tool_calls": [tool_call],
            "finish_reason": "tool_calls",
            "clean_content": None,
        }

    with patch(
        "mcpo.api.routers.chat._call_provider",
        new=AsyncMock(side_effect=endless_tool_calls),
    ) as call_provider, patch(
        "mcpo.api.routers.chat._execute_tool",
        new=AsyncMock(return_value={"ok": True}),
    ) as execute_tool:
        result = await _perform_exchange(
            session,
            MagicMock(),
            MagicMock(),
            payload,
            None,
            None,
            emitter=None,
        )

    assert call_provider.await_count == 3
    assert execute_tool.await_count == 2
    assert result == {
        "role": "assistant",
        "content": "Tool execution stopped after 2 rounds.",
    }
    assert session.steps[-1].detail["finishReason"] == "tool_round_limit"
