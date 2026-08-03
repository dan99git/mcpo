"""Focused contracts for public and streaming chat tool lifecycles."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers.chat import (
    ChatMessageRequest,
    _perform_exchange,
    get_session_manager,
    router,
)
from mcpo.services.chat_sessions import ChatSessionManager


def _assistant_tool_call() -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"query":"Sydney"}',
                },
            }
        ],
    }


def test_session_reload_correlates_public_tool_result_without_mutating_history() -> None:
    manager = ChatSessionManager()
    session = asyncio.run(manager.create_session(model="test-model"))
    session.messages.extend(
        [
            _assistant_tool_call(),
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "lookup",
                "content": json.dumps(
                    {
                        "ok": True,
                        "output": {"temperature": 24},
                        "server": "weather",
                        "tool": "lookup",
                    }
                ),
            },
        ]
    )
    internal_history = copy.deepcopy(session.messages)

    app = FastAPI()
    app.include_router(router, prefix="/chat")

    async def manager_override() -> ChatSessionManager:
        return manager

    app.dependency_overrides[get_session_manager] = manager_override
    with TestClient(app) as client:
        response = client.get(f"/chat/sessions/{session.id}")

    assert response.status_code == 200, response.text
    public_messages = response.json()["session"]["messages"]
    public_call = public_messages[0]["tool_calls"][0]
    assert public_call["status"] == "completed"
    assert public_call["result"] == {
        "ok": True,
        "output": {"temperature": 24},
        "server": "weather",
        "tool": "lookup",
    }
    assert session.messages == internal_history
    assert "result" not in session.messages[0]["tool_calls"][0]
    assert "status" not in session.messages[0]["tool_calls"][0]


class _ToolThenTextStreamClient:
    def __init__(self) -> None:
        self.call_count = 0

    async def chat_completion_stream(
        self,
        *,
        messages,
        model,
        tools=None,
        temperature=None,
        max_output_tokens=None,
        include_reasoning=True,
        reasoning_effort=None,
    ):
        self.call_count += 1
        if self.call_count == 1:
            payloads = [
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": '{"query":',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"Sydney"}'},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ]
        else:
            payloads = [
                {
                    "choices": [
                        {
                            "delta": {
                                "role": "assistant",
                                "content": "Weather loaded.",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            ]

        async def generate():
            for payload in payloads:
                yield f"data: {json.dumps(payload)}\n"
            yield "data: [DONE]\n"

        return generate()


def test_stream_emits_tool_started_once_before_any_delta() -> None:
    asyncio.run(_assert_stream_emits_tool_started_once_before_any_delta())


async def _assert_stream_emits_tool_started_once_before_any_delta() -> None:
    client = _ToolThenTextStreamClient()
    tool = SimpleNamespace(name="lookup")
    session = await ChatSessionManager().create_session(
        model="test-model",
        tool_definitions=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_index={
            "lookup": {
                "server": "weather",
                "session": object(),
                "tool": tool,
                "originalName": "weather.lookup",
            }
        },
    )
    runner = SimpleNamespace(
        execute_tool=AsyncMock(return_value={"temperature": 24})
    )
    events: list[dict] = []

    async def emit(event_type: str, **data) -> None:
        events.append({"type": event_type, **data})

    await _perform_exchange(
        session,
        client,
        runner,
        ChatMessageRequest(
            message="Check Sydney weather.",
            stream=True,
            max_tool_rounds=2,
        ),
        tool_timeout=30,
        tool_timeout_max=60,
        emitter=emit,
    )

    lifecycle = [
        event
        for event in events
        if event["type"].startswith("tool.call.")
    ]
    lifecycle_types = [event["type"] for event in lifecycle]
    assert lifecycle_types == [
        "tool.call.started",
        "tool.call.delta",
        "tool.call.delta",
        "tool.call.result",
    ]
    assert lifecycle_types.count("tool.call.started") == 1
    assert all(event["toolCall"]["id"] == "call-1" for event in lifecycle)
    assert lifecycle[0]["toolCall"]["function"]["name"] == "lookup"
    runner.execute_tool.assert_awaited_once()
