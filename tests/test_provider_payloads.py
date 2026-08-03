from __future__ import annotations

import asyncio
import json

import pytest

from mcpo.providers.anthropic import AnthropicClient, AnthropicError
from mcpo.providers.google import GeminiClient, GeminiError
from mcpo.providers.openai import (
    OpenAIClient,
    OpenAIError,
    _ResponsesStreamNormalizer,
)


PNG_DATA = "iVBORw0KGgo="
PDF_DATA = "JVBERi0xLjQK"
PNG_DATA_URL = f"data:image/png;base64,{PNG_DATA}"
PDF_DATA_URL = f"data:application/pdf;base64,{PDF_DATA}"


def _openai_responses_body(messages, tools=None):
    client = OpenAIClient(api_key="test", use_responses_api=True)
    mapped = client._map_messages(messages, "gpt-5.4")
    return client._prepare_responses_api_body(
        mapped,
        "gpt-5.4",
        tools,
        reasoning_effort=None,
        reasoning_summary=None,
        max_tokens=2048,
    )


def test_openai_responses_maps_multimodal_content_and_function_tools() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "inspect_attachment",
                "description": "Inspect the supplied attachment.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
    ]
    messages = [
        {"role": "system", "content": "Use tools when needed."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect these."},
                {
                    "type": "image_url",
                    "image_url": {"url": PNG_DATA_URL, "detail": "high"},
                },
                {
                    "type": "file",
                    "file": {
                        "filename": "sample.pdf",
                        "file_data": PDF_DATA_URL,
                    },
                },
            ],
        },
    ]

    body = _openai_responses_body(messages, tools)

    assert body["instructions"] == "Use tools when needed."
    assert body["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect these."},
                {
                    "type": "input_image",
                    "image_url": PNG_DATA_URL,
                    "detail": "high",
                },
                {
                    "type": "input_file",
                    "file_data": PDF_DATA_URL,
                    "filename": "sample.pdf",
                },
            ],
        }
    ]
    assert body["tools"] == [
        {
            "type": "function",
            "name": "inspect_attachment",
            "description": "Inspect the supplied attachment.",
            "parameters": tools[0]["function"]["parameters"],
            "strict": True,
        }
    ]


def test_openai_responses_preserves_function_call_and_output_history() -> None:
    messages = [
        {"role": "user", "content": "Check Sydney."},
        {
            "role": "assistant",
            "content": "I will check.",
            "tool_calls": [
                {
                    "id": "call-weather",
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "arguments": json.dumps({"city": "Sydney"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-weather",
            "content": {"temperature": 21},
        },
    ]

    body = _openai_responses_body(messages)

    assert body["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Check Sydney."}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "input_text", "text": "I will check."}],
        },
        {
            "type": "function_call",
            "call_id": "call-weather",
            "name": "weather",
            "arguments": json.dumps({"city": "Sydney"}),
        },
        {
            "type": "function_call_output",
            "call_id": "call-weather",
            "output": json.dumps({"temperature": 21}),
        },
    ]


def test_openai_responses_extracts_current_message_and_function_call_items() -> None:
    client = OpenAIClient(api_key="test")
    response = client._extract_responses_api_response(
        {
            "id": "resp-1",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs-1",
                    "summary": [
                        {"type": "summary_text", "text": "Checked context."}
                    ],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Calling weather."}
                    ],
                },
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-weather",
                    "name": "weather",
                    "arguments": '{"city":"Sydney"}',
                },
            ],
            "usage": {},
        },
        "gpt-5.4",
    )

    message = response["choices"][0]["message"]
    assert message["content"] == "Calling weather."
    assert message["reasoning_content"] == "Checked context."
    assert message["provider_specific"]["reasoning_item_id"] == "rs-1"
    assert message["tool_calls"] == [
        {
            "id": "call-weather",
            "type": "function",
            "function": {
                "name": "weather",
                "arguments": '{"city":"Sydney"}',
            },
        }
    ]


def test_openai_responses_rejects_unknown_content_instead_of_dropping_it() -> None:
    with pytest.raises(OpenAIError, match="Unsupported Responses content block"):
        _openai_responses_body(
            [
                {
                    "role": "user",
                    "content": [{"type": "audio_url", "audio_url": "ignored"}],
                }
            ]
        )


def test_openai_responses_stream_normalizes_text_and_completed_events() -> None:
    normalizer = _ResponsesStreamNormalizer(
        "openai-test", "gpt-5.4", created=123
    )

    text_chunks = normalizer.feed(
        {"type": "response.output_text.delta", "delta": "Hello"}
    )
    completed_chunks = normalizer.feed({"type": "response.completed"})

    assert text_chunks == [
        {
            "id": "openai-test",
            "object": "chat.completion.chunk",
            "created": 123,
            "model": "gpt-5.4",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }
    ]
    assert completed_chunks[0]["choices"][0] == {
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }
    assert normalizer.finished is True
    assert normalizer.feed({"type": "response.completed"}) == []


def test_openai_responses_stream_wires_normalized_events_to_sse(
    monkeypatch,
) -> None:
    captured = {}
    events = [
        {"type": "response.output_text.delta", "delta": "Hello"},
        {"type": "response.completed"},
    ]

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def aiter_lines(self):
            for event in events:
                yield f"data: {json.dumps(event)}"

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def stream(self, method, url, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        "mcpo.providers.openai.httpx.AsyncClient", FakeAsyncClient
    )
    client = OpenAIClient(api_key="test", max_retries=0)

    async def collect_lines():
        iterator = await client.chat_completion_stream(
            messages=[{"role": "user", "content": "Say hello."}],
            model="gpt-5.4",
        )
        return [line async for line in iterator]

    lines = asyncio.run(collect_lines())

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/responses")
    assert captured["json"]["stream"] is True
    assert lines[-1] == "data: [DONE]\n\n"
    chunks = [
        json.loads(line.removeprefix("data: ").strip())
        for line in lines[:-1]
    ]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[1]["choices"][0]["delta"] == {"content": "Hello"}
    assert chunks[2]["choices"][0]["finish_reason"] == "stop"


def test_openai_responses_stream_normalizes_function_argument_events() -> None:
    normalizer = _ResponsesStreamNormalizer(
        "openai-test", "gpt-5.4", created=123
    )

    start = normalizer.feed(
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {
                "id": "fc-1",
                "type": "function_call",
                "call_id": "call-weather",
                "name": "weather",
                "arguments": "",
            },
        }
    )
    arguments = normalizer.feed(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc-1",
            "output_index": 2,
            "delta": '{"city":',
        }
    )
    done = normalizer.feed(
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc-1",
            "output_index": 2,
            "call_id": "call-weather",
            "name": "weather",
            "arguments": '{"city":"Sydney"}',
        }
    )
    completed = normalizer.feed({"type": "response.completed"})

    assert start[0]["choices"][0]["delta"] == {
        "tool_calls": [
            {
                "index": 0,
                "id": "call-weather",
                "type": "function",
                "function": {"name": "weather", "arguments": ""},
            }
        ]
    }
    assert arguments[0]["choices"][0]["delta"] == {
        "tool_calls": [
            {"index": 0, "function": {"arguments": '{"city":'}}
        ]
    }
    assert done == []
    assert completed[0]["choices"][0]["finish_reason"] == "tool_calls"


def test_openai_responses_stream_done_event_recovers_complete_function_call() -> None:
    normalizer = _ResponsesStreamNormalizer(
        "openai-test", "gpt-5.4", created=123
    )

    chunks = normalizer.feed(
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc-1",
            "output_index": 0,
            "call_id": "call-weather",
            "name": "weather",
            "arguments": '{"city":"Sydney"}',
        }
    )

    assert chunks[0]["choices"][0]["delta"] == {
        "tool_calls": [
            {
                "index": 0,
                "id": "call-weather",
                "type": "function",
                "function": {
                    "name": "weather",
                    "arguments": '{"city":"Sydney"}',
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "event,error",
    [
        (
            {"type": "error", "error": {"message": "stream exploded"}},
            "stream exploded",
        ),
        (
            {
                "type": "response.failed",
                "response": {"error": {"message": "response failed"}},
            },
            "response failed",
        ),
    ],
)
def test_openai_responses_stream_raises_terminal_errors(event, error) -> None:
    normalizer = _ResponsesStreamNormalizer(
        "openai-test", "gpt-5.4", created=123
    )

    with pytest.raises(OpenAIError, match=error):
        normalizer.feed(event)

    assert normalizer.finished is True


def test_anthropic_maps_openai_image_and_pdf_blocks_to_native_sources() -> None:
    client = AnthropicClient(api_key="test", enable_prompt_caching=False)

    mapped = client._map_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect these."},
                    {
                        "type": "image_url",
                        "image_url": {"url": PNG_DATA_URL},
                    },
                    {
                        "type": "file",
                        "file": {
                            "filename": "sample.pdf",
                            "file_data": PDF_DATA_URL,
                        },
                    },
                ],
            }
        ]
    )

    assert mapped["messages"][0]["content"] == [
        {"type": "text", "text": "Inspect these."},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": PNG_DATA,
            },
        },
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": PDF_DATA,
            },
        },
    ]


@pytest.mark.parametrize(
    "block,error",
    [
        (
            {
                "type": "image_url",
                "image_url": {"url": "https://example.test/image.png"},
            },
            "Remote image URLs",
        ),
        (
            {
                "type": "file",
                "file": {"file_url": "https://example.test/sample.pdf"},
            },
            "Remote file URLs",
        ),
    ],
)
def test_anthropic_rejects_unsupported_remote_attachment_shapes(
    block, error
) -> None:
    client = AnthropicClient(api_key="test", enable_prompt_caching=False)

    with pytest.raises(AnthropicError, match=error):
        client._map_messages([{"role": "user", "content": [block]}])


def test_anthropic_rejects_unknown_content_instead_of_dropping_it() -> None:
    client = AnthropicClient(api_key="test", enable_prompt_caching=False)

    with pytest.raises(AnthropicError, match="Unsupported Anthropic content block"):
        client._map_messages(
            [{"role": "user", "content": [{"type": "audio_url"}]}]
        )


def test_gemini_preserves_image_mime_and_maps_pdf_to_inline_data() -> None:
    client = GeminiClient(api_key="test", enable_context_caching=False)

    mapped = client._map_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect these."},
                    {
                        "type": "image_url",
                        "image_url": {"url": PNG_DATA_URL},
                    },
                    {
                        "type": "file",
                        "file": {
                            "filename": "sample.pdf",
                            "file_data": PDF_DATA_URL,
                        },
                    },
                ],
            }
        ]
    )

    assert mapped["contents"][0]["parts"] == [
        {"text": "Inspect these."},
        {"inlineData": {"mimeType": "image/png", "data": PNG_DATA}},
        {"inlineData": {"mimeType": "application/pdf", "data": PDF_DATA}},
    ]


@pytest.mark.parametrize(
    "block,error",
    [
        (
            {
                "type": "image_url",
                "image_url": {"url": "https://example.test/image.png"},
            },
            "Remote image URLs",
        ),
        (
            {
                "type": "file",
                "file": {"file_url": "https://example.test/sample.pdf"},
            },
            "Remote file URLs",
        ),
    ],
)
def test_gemini_rejects_unsupported_remote_attachment_shapes(
    block, error
) -> None:
    client = GeminiClient(api_key="test", enable_context_caching=False)

    with pytest.raises(GeminiError, match=error):
        client._map_messages([{"role": "user", "content": [block]}])


def test_gemini_rejects_unknown_content_instead_of_dropping_it() -> None:
    client = GeminiClient(api_key="test", enable_context_caching=False)

    with pytest.raises(GeminiError, match="Unsupported Gemini content block"):
        client._map_messages(
            [{"role": "user", "content": [{"type": "audio_url"}]}]
        )
