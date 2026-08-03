import json

import pytest

from mcpo.api.routers.completions import (
    AnthropicProvider,
    CompletionProviderError,
    CompletionRequest,
    OpenAICompatibleProvider,
)


def _tool_history() -> CompletionRequest:
    return CompletionRequest(
        model="test-model",
        messages=[
            {"role": "user", "content": "Check the weather."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
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
                "tool_call_id": "call-1",
                "content": json.dumps({"temperature": 21}),
            },
        ],
    )


def test_openai_payload_preserves_assistant_tool_call_history() -> None:
    payload = _tool_history()
    provider = OpenAICompatibleProvider(
        "openai",
        base_url="https://example.invalid/v1",
        api_key="test",
    )

    body = provider._build_payload(payload, stream=False)

    assistant = body["messages"][1]
    tool_result = body["messages"][2]
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert assistant["tool_calls"][0]["function"]["name"] == "weather"
    assert tool_result["tool_call_id"] == "call-1"


def test_openai_payload_serializes_tool_definitions_as_an_array() -> None:
    payload = CompletionRequest(
        model="test-model",
        messages=[{"role": "user", "content": "Use the tool."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "demo",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    provider = OpenAICompatibleProvider(
        "openai",
        base_url="https://example.invalid/v1",
        api_key="test",
    )

    body = provider._build_payload(payload, stream=False)

    assert isinstance(body["messages"], list)
    assert isinstance(body["tools"], list)
    assert body["tools"][0]["function"]["name"] == "demo"


def test_anthropic_payload_maps_tool_use_and_tool_result() -> None:
    payload = _tool_history()
    provider = AnthropicProvider(
        base_url="https://example.invalid",
        api_key="test",
    )

    mapped = provider._map_messages(payload)

    tool_use = mapped["messages"][1]["content"][0]
    tool_result = mapped["messages"][2]["content"][0]
    assert tool_use == {
        "type": "tool_use",
        "id": "call-1",
        "name": "weather",
        "input": {"city": "Sydney"},
    }
    assert tool_result == {
        "type": "tool_result",
        "tool_use_id": "call-1",
        "content": json.dumps({"temperature": 21}),
    }


def test_anthropic_rejects_invalid_tool_arguments() -> None:
    payload = _tool_history()
    payload.messages[1].tool_calls[0]["function"]["arguments"] = "not-json"
    provider = AnthropicProvider(
        base_url="https://example.invalid",
        api_key="test",
    )

    with pytest.raises(CompletionProviderError, match="Invalid JSON arguments"):
        provider._map_messages(payload)
