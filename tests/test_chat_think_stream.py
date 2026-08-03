"""Focused contracts for inline thinking streamed across chunk boundaries."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcpo.api.routers.chat import ChatMessageRequest, _call_provider_stream


OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"


class _DeltaStreamClient:
    def __init__(self, deltas: list[dict[str, Any]]) -> None:
        self.deltas = deltas

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
        async def generate():
            for index, delta in enumerate(self.deltas):
                payload = {
                    "choices": [
                        {
                            "delta": delta,
                            "finish_reason": (
                                "stop" if index == len(self.deltas) - 1 else None
                            ),
                        }
                    ]
                }
                yield f"data: {json.dumps(payload)}\n"
            yield "data: [DONE]\n"

        return generate()


def _content_deltas(chunks: list[str]) -> list[dict[str, Any]]:
    return [
        {
            **({"role": "assistant"} if index == 0 else {}),
            "content": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]


async def _run_stream(
    deltas: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    async def emit(event_type: str, **data: Any) -> None:
        events.append({"type": event_type, **data})

    result = await _call_provider_stream(
        messages=[{"role": "user", "content": "test"}],
        tools=None,
        client=_DeltaStreamClient(deltas),
        payload=ChatMessageRequest(message="test", stream=True),
        model="test-model",
        emitter=emit,
    )
    return result, events


def _observable(
    result: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "clean_content": result["clean_content"],
        "reasoning": result["reasoning"],
        "message_content": result["message"]["content"],
        "content_events": "".join(
            event["text"] for event in events if event["type"] == "message.delta"
        ),
        "reasoning_events": "".join(
            event["text"] for event in events if event["type"] == "reasoning.delta"
        ),
    }


@pytest.mark.asyncio
async def test_inline_think_tags_match_one_chunk_at_every_boundary() -> None:
    prefix = "Visible before. "
    reasoning = "Private reasoning."
    suffix = " Visible after."
    complete = f"{prefix}{OPEN_TAG}{reasoning}{CLOSE_TAG}{suffix}"
    baseline = _observable(*await _run_stream(_content_deltas([complete])))

    candidates: list[tuple[str, list[str]]] = []
    for boundary in range(1, len(OPEN_TAG)):
        candidates.append(
            (
                f"opening tag boundary {boundary}",
                [
                    f"{prefix}{OPEN_TAG[:boundary]}",
                    f"{OPEN_TAG[boundary:]}{reasoning}{CLOSE_TAG}{suffix}",
                ],
            )
        )
    for boundary in range(1, len(CLOSE_TAG)):
        candidates.append(
            (
                f"closing tag boundary {boundary}",
                [
                    f"{prefix}{OPEN_TAG}{reasoning}{CLOSE_TAG[:boundary]}",
                    f"{CLOSE_TAG[boundary:]}{suffix}",
                ],
            )
        )
    candidates.append(("every character", list(complete)))

    for label, chunks in candidates:
        observed = _observable(*await _run_stream(_content_deltas(chunks)))
        assert observed == baseline, label


@pytest.mark.asyncio
async def test_partial_opening_tag_is_flushed_as_content_at_eof() -> None:
    for boundary in range(1, len(OPEN_TAG)):
        content = f"Visible {OPEN_TAG[:boundary]}"
        result, events = await _run_stream(_content_deltas([content]))
        assert result["clean_content"] == content, boundary
        assert result["reasoning"] is None, boundary
        assert "".join(
            event["text"]
            for event in events
            if event["type"] == "message.delta"
        ) == content, boundary


@pytest.mark.asyncio
async def test_unclosed_reasoning_and_partial_close_are_retained_at_eof() -> None:
    for boundary in range(0, len(CLOSE_TAG)):
        dangling_close = CLOSE_TAG[:boundary]
        result, events = await _run_stream(
            _content_deltas([f"{OPEN_TAG}unfinished{dangling_close}"])
        )
        expected_reasoning = f"unfinished{dangling_close}"
        assert result["clean_content"] == "", boundary
        assert result["reasoning"] == expected_reasoning, boundary
        assert "".join(
            event["text"]
            for event in events
            if event["type"] == "reasoning.delta"
        ) == expected_reasoning, boundary


@pytest.mark.asyncio
async def test_explicit_reasoning_fields_remain_separate_from_content_parser() -> None:
    result, events = await _run_stream(
        [
            {
                "role": "assistant",
                "reasoning_content": "Explicit ",
                "content": "Visible answer.",
            },
            {"reasoning_content": "reasoning."},
        ]
    )

    assert result["clean_content"] == "Visible answer."
    assert result["reasoning"] == "Explicit reasoning."
    assert result["message"]["reasoning_content"] == "Explicit reasoning."
    assert "".join(
        event["text"]
        for event in events
        if event["type"] == "reasoning.delta"
    ) == "Explicit reasoning."


@pytest.mark.asyncio
async def test_reasoning_details_are_preserved_while_content_streams() -> None:
    result, _events = await _run_stream(
        [
            {
                "role": "assistant",
                "reasoning_details": [
                    {
                        "type": "reasoning.text",
                        "id": "reason-1",
                        "index": 0,
                        "format": "provider-v1",
                        "text": "First ",
                    }
                ],
                "content": "Visible answer.",
            },
            {
                "reasoning_details": [
                    {
                        "type": "reasoning.text",
                        "id": "reason-1",
                        "index": 0,
                        "format": "provider-v1",
                        "text": "second.",
                    }
                ]
            },
        ]
    )

    assert result["clean_content"] == "Visible answer."
    assert result["reasoning"] == "First second."
    assert result["message"]["reasoning_details"] == [
        {
            "type": "reasoning.text",
            "id": "reason-1",
            "format": "provider-v1",
            "index": 0,
            "text": "First second.",
        }
    ]
