from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, TypedDict

import httpx

# --- Configuration & Constants ---
logger = logging.getLogger("openai_client")

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com"
OPENAI_API_VERSION = "v1"

class OpenAIError(RuntimeError):
    """Base exception for OpenAI API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

# --- Type Definitions ---
class ProviderSpecific(TypedDict, total=False):
    reasoning_item_id: Optional[str]  # For Responses API
    reasoning_tokens: Optional[int]
    cached_tokens: Optional[int]  # OpenAI doesn't distinguish creation vs read

# --- Helper Functions ---

def _as_json_str(content: Any) -> str:
    """Safely serialize content to JSON string."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)

def _json(obj: Any) -> str:
    """Compact JSON serialization."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OpenAIError(f"{label} must be a non-empty string")
    return value


def _responses_file_block(block: Dict[str, Any]) -> Dict[str, Any]:
    source = block.get("file") if block.get("type") == "file" else block
    if not isinstance(source, dict):
        raise OpenAIError("Responses file content must contain a 'file' object")

    source_keys = [
        key
        for key in ("file_data", "file_id", "file_url")
        if source.get(key) is not None
    ]
    if len(source_keys) != 1:
        raise OpenAIError(
            "Responses file content must provide exactly one of "
            "file_data, file_id, or file_url"
        )

    source_key = source_keys[0]
    result: Dict[str, Any] = {
        "type": "input_file",
        source_key: _required_string(
            source[source_key], f"Responses {source_key}"
        ),
    }
    if source.get("filename") is not None:
        result["filename"] = _required_string(
            source["filename"], "Responses filename"
        )
    return result


def _responses_input_content(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        raise OpenAIError(
            "Responses user content must be a string or an array of content blocks"
        )

    mapped: List[Dict[str, Any]] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            raise OpenAIError(
                f"Responses content block {index} must be an object"
            )
        block_type = block.get("type")
        if block_type in ("text", "input_text"):
            text = block.get("text")
            if not isinstance(text, str):
                raise OpenAIError(
                    f"Responses text block {index} must contain string text"
                )
            mapped.append({"type": "input_text", "text": text})
            continue

        if block_type in ("image_url", "input_image"):
            image = block.get("image_url")
            detail = block.get("detail")
            if isinstance(image, dict):
                detail = image.get("detail", detail)
                image = image.get("url")

            result: Dict[str, Any] = {"type": "input_image"}
            if image is not None:
                result["image_url"] = _required_string(
                    image, f"Responses image block {index} URL"
                )
            elif block.get("file_id") is not None:
                result["file_id"] = _required_string(
                    block["file_id"], f"Responses image block {index} file_id"
                )
            else:
                raise OpenAIError(
                    f"Responses image block {index} needs image_url or file_id"
                )
            if detail is not None:
                if detail not in ("auto", "low", "high"):
                    raise OpenAIError(
                        f"Responses image block {index} has invalid detail '{detail}'"
                    )
                result["detail"] = detail
            mapped.append(result)
            continue

        if block_type in ("file", "input_file"):
            mapped.append(_responses_file_block(block))
            continue

        raise OpenAIError(
            f"Unsupported Responses content block type '{block_type}' at index {index}"
        )

    if not mapped:
        raise OpenAIError("Responses content must contain at least one block")
    return mapped


def _responses_assistant_content(content: Any) -> List[Dict[str, Any]]:
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        raise OpenAIError(
            "Responses assistant content must be a string or an array of text blocks"
        )

    mapped: List[Dict[str, Any]] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            raise OpenAIError(
                f"Responses assistant content block {index} must be an object"
            )
        block_type = block.get("type")
        if block_type not in ("text", "input_text", "output_text"):
            raise OpenAIError(
                "Unsupported Responses assistant content block type "
                f"'{block_type}' at index {index}"
            )
        text = block.get("text")
        if not isinstance(text, str):
            raise OpenAIError(
                f"Responses assistant text block {index} must contain string text"
            )
        mapped.append({"type": "input_text", "text": text})
    return mapped


def _responses_function_tools(
    tools: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    mapped: List[Dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise OpenAIError(f"Responses tool {index} must be an object")
        if tool.get("type") != "function":
            mapped.append(dict(tool))
            continue

        function = tool.get("function")
        if function is None:
            function = tool
        if not isinstance(function, dict):
            raise OpenAIError(
                f"Responses function tool {index} must contain a function object"
            )

        result: Dict[str, Any] = {
            "type": "function",
            "name": _required_string(
                function.get("name"), f"Responses function tool {index} name"
            ),
            "parameters": function.get("parameters", {}),
        }
        if function.get("description") is not None:
            result["description"] = function["description"]
        strict = function.get("strict", tool.get("strict"))
        if strict is not None:
            result["strict"] = strict
        mapped.append(result)
    return mapped


class _ResponsesStreamNormalizer:
    def __init__(
        self,
        message_id: str,
        model: str,
        *,
        created: Optional[int] = None,
    ) -> None:
        self.message_id = message_id
        self.model = model
        self.created = int(time.time()) if created is None else created
        self.finished = False
        self.saw_tool_call = False
        self.reasoning_item_id: Optional[str] = None
        self._tool_calls: Dict[str, Dict[str, Any]] = {}

    def _chunk(
        self,
        delta: Dict[str, Any],
        finish_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "id": self.message_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }],
        }

    @staticmethod
    def _stream_error(event: Dict[str, Any]) -> OpenAIError:
        response = event.get("response")
        error = event.get("error")
        if error is None and isinstance(response, dict):
            error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
        else:
            message = error
        if not message:
            message = event.get("message") or event.get("type") or "unknown error"
        return OpenAIError(
            f"OpenAI Responses stream error: {message}",
            body=event,
        )

    def _tool_state(
        self,
        item_id: str,
    ) -> Dict[str, Any]:
        state = self._tool_calls.setdefault(
            item_id,
            {
                "index": len(self._tool_calls),
                "call_id": None,
                "name": None,
                "announced": False,
                "arguments_emitted": False,
            },
        )
        return state

    def feed(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(event, dict):
            raise OpenAIError("OpenAI Responses stream event must be an object")

        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            if not isinstance(delta, str):
                raise OpenAIError("Responses output text delta must be a string")
            return [self._chunk({"content": delta})] if delta else []

        if event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            delta = event.get("delta", "")
            if not isinstance(delta, str):
                raise OpenAIError("Responses reasoning delta must be a string")
            return [self._chunk({"reasoning_content": delta})] if delta else []

        if event_type == "response.output_item.added":
            item = event.get("item")
            if not isinstance(item, dict):
                return []
            if item.get("type") == "reasoning":
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    self.reasoning_item_id = item_id
                return []
            if item.get("type") != "function_call":
                return []

            item_id = _required_string(
                item.get("id") or event.get("item_id"),
                "Responses streamed function item id",
            )
            call_id = _required_string(
                item.get("call_id"), "Responses streamed function call_id"
            )
            name = _required_string(
                item.get("name"), "Responses streamed function name"
            )
            arguments = item.get("arguments", "")
            if not isinstance(arguments, str):
                raise OpenAIError(
                    "Responses streamed function arguments must be a string"
                )
            state = self._tool_state(item_id)
            state.update({
                "call_id": call_id,
                "name": name,
                "announced": True,
                "arguments_emitted": bool(arguments),
            })
            self.saw_tool_call = True
            return [self._chunk({
                "tool_calls": [{
                    "index": state["index"],
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }]
            })]

        if event_type == "response.function_call_arguments.delta":
            item_id = _required_string(
                event.get("item_id"), "Responses function argument item_id"
            )
            delta = event.get("delta", "")
            if not isinstance(delta, str):
                raise OpenAIError(
                    "Responses function argument delta must be a string"
                )
            if not delta:
                return []
            state = self._tool_state(item_id)
            state["arguments_emitted"] = True
            self.saw_tool_call = True
            return [self._chunk({
                "tool_calls": [{
                    "index": state["index"],
                    "function": {"arguments": delta},
                }]
            })]

        if event_type == "response.function_call_arguments.done":
            item_id = _required_string(
                event.get("item_id"), "Responses completed function item_id"
            )
            state = self._tool_state(item_id)
            call_id = event.get("call_id") or state.get("call_id")
            name = event.get("name") or state.get("name")
            arguments = event.get("arguments", "")
            if not isinstance(arguments, str):
                raise OpenAIError(
                    "Responses completed function arguments must be a string"
                )
            self.saw_tool_call = True

            if not state["announced"]:
                call_id = _required_string(
                    call_id, "Responses completed function call_id"
                )
                name = _required_string(name, "Responses completed function name")
                state.update({
                    "call_id": call_id,
                    "name": name,
                    "announced": True,
                    "arguments_emitted": bool(arguments),
                })
                return [self._chunk({
                    "tool_calls": [{
                        "index": state["index"],
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }]
                })]

            if arguments and not state["arguments_emitted"]:
                state["arguments_emitted"] = True
                return [self._chunk({
                    "tool_calls": [{
                        "index": state["index"],
                        "function": {"arguments": arguments},
                    }]
                })]
            return []

        if event_type in ("error", "response.failed", "response.cancelled"):
            self.finished = True
            raise self._stream_error(event)

        if event_type in ("response.completed", "response.incomplete"):
            if self.finished:
                return []
            chunks: List[Dict[str, Any]] = []
            if self.reasoning_item_id:
                chunks.append(self._chunk({
                    "provider_specific": {
                        "reasoning_item_id": self.reasoning_item_id
                    }
                }))

            finish_reason = "tool_calls" if self.saw_tool_call else "stop"
            if event_type == "response.incomplete":
                response = event.get("response")
                details = (
                    response.get("incomplete_details", {})
                    if isinstance(response, dict)
                    else {}
                )
                if details.get("reason") == "max_output_tokens":
                    finish_reason = "length"
            chunks.append(self._chunk({}, finish_reason))
            self.finished = True
            return chunks

        return []


def _is_reasoning_model(model: str) -> bool:
    """
    Check if model is a reasoning model that uses max_completion_tokens 
    and reasoning_effort instead of temperature.
    
    Includes: o1, o1-pro, o3, o4, gpt-5.x, codex-mini
    """
    m = model.lower()
    return (
        m.startswith("o1") or 
        m.startswith("o3") or 
        m.startswith("o4") or
        m.startswith("gpt-5") or
        "gpt-5" in m or  # Catches gpt-5.1, gpt-5.5, etc.
        m.startswith("codex")  # codex-mini is a reasoning model
    )

def _supports_responses_api(model: str) -> bool:
    """
    Check if model supports the Responses API (stateful, reasoning summaries).
    
    Responses API supported: o1-pro, o3, o4, gpt-5.x, codex-mini
    Note: o1-mini/o1-preview use Chat Completions, o1-pro uses Responses API
    """
    m = model.lower()
    return (
        "o1-pro" in m or  # o1-pro, o1-pro-preview
        "o3" in m or 
        "o4" in m or
        "gpt-5" in m or  # gpt-5, gpt-5.1, gpt-5.5, gpt-5-mini, etc.
        m.startswith("codex")  # codex-mini
    )

class OpenAIClient:
    """
    Advanced Async Client for OpenAI GPT-4, GPT-5, and o-series models.
    
    Feature parity with Anthropic/Gemini clients:
    1. Reasoning Control: Support for reasoning_effort (low/medium/high/minimal)
    2. Reasoning Summaries: Access to reasoning_item_id for state persistence (Responses API)
    3. Context Caching: Automatic prompt caching for long contexts
    4. Tool Calling: Full function calling with parallel execution support
    5. Streaming: Real-time SSE streaming for both Chat Completions and Responses API
    6. State Persistence: Provider-specific metadata for agent resumption
    7. Dual API Support: Chat Completions (legacy) + Responses API (stateful)
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        stream_timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        use_responses_api: Optional[bool] = None
    ) -> None:
        # Support both OPEN_AI_API_KEY and OPENAI_API_KEY env vars
        self._api_key = api_key or os.getenv("OPEN_AI_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise OpenAIError(
                "OPEN_AI_API_KEY or OPENAI_API_KEY environment variable is required"
            )
        
        # Base URL    
        self._base_url = (
            base_url or 
            os.getenv("OPEN_AI_BASE_URL") or
            os.getenv("OPENAI_BASE_URL") or 
            OPENAI_DEFAULT_BASE_URL
        ).rstrip("/")
        
        # Request timeout
        self._timeout = (
            timeout if timeout is not None else
            float(os.getenv("OPEN_AI_TIMEOUT_SECONDS", "120"))
        )
        
        # Stream timeout (longer for SSE)
        self._stream_timeout = (
            stream_timeout if stream_timeout is not None else
            float(os.getenv("OPEN_AI_STREAM_TIMEOUT_SECONDS", "300"))
        )
        
        # Retry count
        self._max_retries = (
            max_retries if max_retries is not None else
            int(os.getenv("OPEN_AI_MAX_RETRIES", "2"))
        )
        
        # Default reasoning effort for reasoning models
        self._default_reasoning_effort = (
            reasoning_effort or
            os.getenv("OPEN_AI_REASONING_EFFORT")  # None if not set
        )
        
        # Prefer Responses API for reasoning models
        self._use_responses_api = (
            use_responses_api if use_responses_api is not None else
            os.getenv("OPEN_AI_USE_RESPONSES_API", "true").lower() in ("true", "1", "yes")
        )

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _should_use_responses_api(self, model: str) -> bool:
        """Determine if we should use Responses API for this model."""
        return self._use_responses_api and _supports_responses_api(model)

    def _reconstruct_assistant_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Critical for agent state resumption.
        Reconstructs message with reasoning_item_id for Responses API.
        """
        content = msg.get("content", "")
        role = msg.get("role", "assistant")
        
        message = {"role": role, "content": content}
        
        # Re-inject tool calls
        if "tool_calls" in msg:
            message["tool_calls"] = msg["tool_calls"]
        
        # Note: reasoning_item_id is preserved in provider_specific
        # but NOT sent back to the API - it's for tracking only
        
        return message

    def _map_messages(
        self, 
        messages: Iterable[Dict[str, Any]],
        model: str
    ) -> List[Dict[str, Any]]:
        """
        Transforms generic chat history into OpenAI format.
        Handles system messages, developer messages (for reasoning models), and tool results.
        """
        formatted_messages = []
        
        for msg in messages:
            role = msg.get("role", "").lower()
            content = msg.get("content")

            # System message handling
            if role == "system":
                # For reasoning models (o3, o4, gpt-5), convert to developer message
                if _is_reasoning_model(model):
                    formatted_messages.append({
                        "role": "developer",
                        "content": _as_json_str(content)
                    })
                else:
                    formatted_messages.append({
                        "role": "system",
                        "content": _as_json_str(content)
                    })
                continue

            # Assistant message
            if role == "assistant":
                formatted_messages.append(self._reconstruct_assistant_message(msg))
                continue

            # Tool result
            if role == "tool":
                formatted_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id") or msg.get("id"),
                    "content": _as_json_str(content)
                })
                continue

            # User message
            if role == "user":
                if isinstance(content, str):
                    formatted_messages.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Multimodal content (images, etc.)
                    formatted_messages.append({"role": "user", "content": content})
                continue

        return formatted_messages

    def _extract_chat_completion_response(
        self, 
        data: Dict[str, Any], 
        model: str
    ) -> Dict[str, Any]:
        """
        Parses Chat Completions API response.
        """
        choices = data.get("choices", [])
        if not choices:
            raise OpenAIError("No choices in response")
        
        choice = choices[0]
        message = choice.get("message", {})
        
        # Build message
        result_message = {
            "role": message.get("role", "assistant"),
            "content": message.get("content", "")
        }
        
        if message.get("tool_calls"):
            result_message["tool_calls"] = message["tool_calls"]

        # Usage stats
        usage = data.get("usage", {})
        
        # Provider-specific metadata
        # FIXED: OpenAI only returns cached_tokens (not separate creation/read)
        cached_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens")
        reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens")
        
        provider_specific: ProviderSpecific = {}
        
        if reasoning_tokens is not None:
            provider_specific["reasoning_tokens"] = reasoning_tokens
        if cached_tokens is not None:
            provider_specific["cached_tokens"] = cached_tokens
        
        # Only add if we have relevant data
        if provider_specific:
            result_message["provider_specific"] = provider_specific

        return {
            "id": data.get("id"),
            "object": "chat.completion",
            "created": data.get("created", int(time.time())),
            "model": model,
            "usage": usage,
            "choices": [{
                "index": 0,
                "message": result_message,
                "finish_reason": choice.get("finish_reason", "stop")
            }]
        }

    def _extract_responses_api_response(
        self,
        data: Dict[str, Any],
        model: str
    ) -> Dict[str, Any]:
        """
        Parses Responses API response.
        Extracts reasoning summaries and reasoning_item_id for state persistence.
        """
        outputs = data.get("output", [])
        
        final_text = ""
        reasoning_summary = ""
        tool_calls = []
        reasoning_item_id = None
        reasoning_tokens = 0
        
        for output in outputs:
            output_type = output.get("type")

            if output_type == "message":
                for block in output.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in ("output_text", "text"):
                        final_text += block.get("text", "")

            elif output_type == "text":
                final_text += output.get("content", "")

            elif output_type == "reasoning":
                # Reasoning summary (only available with Responses API)
                summary = output.get("summary", "")
                if isinstance(summary, list):
                    reasoning_summary += "".join(
                        item.get("text", "")
                        for item in summary
                        if isinstance(item, dict)
                        and item.get("type") == "summary_text"
                    )
                elif isinstance(summary, str):
                    reasoning_summary += summary
                reasoning_item_id = output.get("id")

            elif output_type == "function_call":
                tool_calls.append({
                    "id": output.get("call_id") or output.get("id"),
                    "type": "function",
                    "function": {
                        "name": output.get("name", ""),
                        "arguments": output.get("arguments", "{}")
                    }
                })

            elif output_type == "tool_call":
                fn = output.get("function", {})
                tool_calls.append({
                    "id": output.get("call_id") or output.get("id"),
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}")
                    }
                })

        # Build message
        message = {
            "role": "assistant",
            "content": final_text
        }
        
        if tool_calls:
            message["tool_calls"] = tool_calls
        
        if reasoning_summary:
            message["reasoning_content"] = reasoning_summary

        # Usage stats
        usage = data.get("usage", {})
        reasoning_tokens = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)
        cached_tokens = usage.get("input_tokens_details", {}).get("cached_tokens")
        
        # Provider-specific metadata for state persistence
        provider_specific: ProviderSpecific = {}
        
        if reasoning_item_id:
            provider_specific["reasoning_item_id"] = reasoning_item_id
        if reasoning_tokens:
            provider_specific["reasoning_tokens"] = reasoning_tokens
        if cached_tokens is not None:
            provider_specific["cached_tokens"] = cached_tokens
        
        if provider_specific:
            message["provider_specific"] = provider_specific

        return {
            "id": data.get("id"),
            "object": "chat.completion",
            "created": data.get("created_at", int(time.time())),
            "model": model,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0)
            },
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": data.get("status", "completed")
            }]
        }

    def _prepare_chat_completion_body(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: int,
        reasoning_effort: Optional[str],
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Prepare body for Chat Completions API."""
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        
        # Reasoning models use max_completion_tokens
        if _is_reasoning_model(model):
            body["max_completion_tokens"] = max_tokens
            
            # Reasoning effort (o3-mini, o1, o4-mini)
            if reasoning_effort:
                valid_efforts = ["low", "medium", "high"]
                # GPT-5 also supports "minimal"
                if model.lower().startswith("gpt-5"):
                    valid_efforts.append("minimal")
                
                if reasoning_effort.lower() not in valid_efforts:
                    logger.warning(f"Invalid reasoning_effort '{reasoning_effort}'. Using 'medium'.")
                    reasoning_effort = "medium"
                
                body["reasoning_effort"] = reasoning_effort.lower()
            
            # Reasoning models don't support temperature
            if temperature is not None:
                logger.warning(f"Reasoning model '{model}' does not support temperature. Ignoring.")
        else:
            # Standard models
            body["max_tokens"] = max_tokens
            if temperature is not None:
                body["temperature"] = temperature

        # Tools
        if tools:
            body["tools"] = list(tools)

        return body

    def _prepare_responses_api_body(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]],
        reasoning_effort: Optional[str],
        reasoning_summary: Optional[str],
        max_tokens: int,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Prepare body for Responses API."""
        input_items: List[Dict[str, Any]] = []
        instruction_parts: List[str] = []
        for message_index, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content")

            if role in ("system", "developer"):
                if not isinstance(content, str):
                    raise OpenAIError(
                        f"Responses {role} message {message_index} content must be text"
                    )
                if content:
                    instruction_parts.append(content)
                continue

            if role == "user":
                input_items.append({
                    "type": "message",
                    "role": "user",
                    "content": _responses_input_content(content),
                })
                continue

            if role == "assistant":
                assistant_content = _responses_assistant_content(content)
                if assistant_content:
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": assistant_content,
                    })

                tool_calls = msg.get("tool_calls") or []
                if not isinstance(tool_calls, list):
                    raise OpenAIError(
                        f"Responses assistant message {message_index} tool_calls must be an array"
                    )
                for tool_index, tool_call in enumerate(tool_calls):
                    if not isinstance(tool_call, dict):
                        raise OpenAIError(
                            f"Responses tool call {tool_index} must be an object"
                        )
                    if tool_call.get("type") not in (None, "function"):
                        raise OpenAIError(
                            "Unsupported Responses assistant tool call type "
                            f"'{tool_call.get('type')}'"
                        )
                    function = tool_call.get("function")
                    if not isinstance(function, dict):
                        raise OpenAIError(
                            f"Responses tool call {tool_index} needs a function object"
                        )
                    arguments = function.get("arguments", "{}")
                    if not isinstance(arguments, str):
                        if isinstance(arguments, (dict, list)):
                            arguments = _json(arguments)
                        else:
                            raise OpenAIError(
                                f"Responses tool call {tool_index} arguments must be JSON text"
                            )
                    input_items.append({
                        "type": "function_call",
                        "call_id": _required_string(
                            tool_call.get("id"),
                            f"Responses tool call {tool_index} id",
                        ),
                        "name": _required_string(
                            function.get("name"),
                            f"Responses tool call {tool_index} name",
                        ),
                        "arguments": arguments,
                    })
                continue

            if role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": _required_string(
                        msg.get("tool_call_id") or msg.get("id"),
                        f"Responses tool result {message_index} call id",
                    ),
                    "output": _as_json_str(content),
                })
                continue

            raise OpenAIError(
                f"Unsupported Responses message role '{role}' at index {message_index}"
            )

        # Fallback: if no non-system messages, use empty string
        input_content = input_items if input_items else ""

        body: Dict[str, Any] = {
            "model": model,
            "input": input_content,
            "max_output_tokens": max_tokens
        }

        if instruction_parts:
            body["instructions"] = "\n\n".join(instruction_parts)

        # Reasoning configuration
        if reasoning_effort or reasoning_summary:
            reasoning_config: Dict[str, Any] = {}
            
            if reasoning_effort:
                valid_efforts = ["low", "medium", "high"]
                if model.lower().startswith("gpt-5"):
                    valid_efforts.append("minimal")
                
                if reasoning_effort.lower() not in valid_efforts:
                    logger.warning(f"Invalid reasoning_effort. Using 'medium'.")
                    reasoning_effort = "medium"
                
                reasoning_config["effort"] = reasoning_effort.lower()
            
            if reasoning_summary:
                # "auto", "detailed", "none"
                if reasoning_summary not in ["auto", "detailed", "none"]:
                    reasoning_summary = "auto"
                reasoning_config["summary"] = reasoning_summary
            
            body["reasoning"] = reasoning_config

        # Tools
        if tools:
            body["tools"] = _responses_function_tools(tools)

        return body

    async def chat_completion(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        reasoning_effort: Optional[str] = None,
        reasoning_summary: Optional[str] = "auto",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Execute a non-streaming chat completion.
        
        Args:
            reasoning_effort (str): "low", "medium", "high" (GPT-5: also "minimal")
            reasoning_summary (str): "auto", "detailed", "none" (Responses API only)
        """
        # Use env default if not specified
        effective_reasoning_effort = reasoning_effort or self._default_reasoning_effort
        
        use_responses_api = self._should_use_responses_api(model)
        formatted_messages = self._map_messages(messages, model)
        
        if use_responses_api:
            # Responses API (stateful, reasoning summaries)
            body = self._prepare_responses_api_body(
                formatted_messages, model, tools, effective_reasoning_effort,
                reasoning_summary, max_tokens, **kwargs
            )
            url = f"{self._base_url}/{OPENAI_API_VERSION}/responses"
        else:
            # Chat Completions API (standard)
            body = self._prepare_chat_completion_body(
                formatted_messages, model, tools, temperature,
                max_tokens, effective_reasoning_effort, **kwargs
            )
            url = f"{self._base_url}/{OPENAI_API_VERSION}/chat/completions"
        
        headers = self._get_headers()
        
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, headers=headers, json=body)
                    
                    if resp.status_code == 429 or resp.status_code >= 500:
                        if attempt < self._max_retries:
                            wait_time = 2 ** attempt
                            logger.warning(f"OpenAI API {resp.status_code}. Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                    
                    if resp.status_code >= 400:
                        error_data = resp.json() if resp.text else {}
                        error_msg = error_data.get("error", {}).get("message", resp.text)
                        raise OpenAIError(
                            f"OpenAI API Error {resp.status_code}: {error_msg}",
                            status_code=resp.status_code,
                            body=resp.text
                        )
                    
                    data = resp.json()
                    
                    if use_responses_api:
                        return self._extract_responses_api_response(data, model)
                    else:
                        return self._extract_chat_completion_response(data, model)

            except httpx.RequestError as e:
                if attempt < self._max_retries:
                    logger.warning(f"Network error: {e}. Retrying...")
                    await asyncio.sleep(1)
                    continue
                raise OpenAIError(f"Connection failed after {self._max_retries} retries: {str(e)}")

        raise OpenAIError("Max retries exceeded")

    async def chat_completion_stream(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        reasoning_effort: Optional[str] = None,
        reasoning_summary: Optional[str] = "auto",
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Execute a streaming chat completion.
        
        Yields:
            Server-Sent Event (SSE) formatted strings: "data: {json}\n\n"
        """
        # Use env default if not specified
        effective_reasoning_effort = reasoning_effort or self._default_reasoning_effort
        
        use_responses_api = self._should_use_responses_api(model)
        formatted_messages = self._map_messages(messages, model)
        
        if use_responses_api:
            body = self._prepare_responses_api_body(
                formatted_messages, model, tools, effective_reasoning_effort,
                reasoning_summary, max_tokens, **kwargs
            )
            body["stream"] = True
            url = f"{self._base_url}/{OPENAI_API_VERSION}/responses"
        else:
            body = self._prepare_chat_completion_body(
                formatted_messages, model, tools, temperature,
                max_tokens, effective_reasoning_effort, **kwargs
            )
            body["stream"] = True
            url = f"{self._base_url}/{OPENAI_API_VERSION}/chat/completions"
        
        headers = self._get_headers()

        async def _stream_generator() -> AsyncIterator[str]:
            for attempt in range(self._max_retries + 1):
                try:
                    # FIXED: Use bounded timeout instead of None
                    async with httpx.AsyncClient(timeout=self._stream_timeout) as client:
                        async with client.stream("POST", url, headers=headers, json=body) as response:
                            if response.status_code >= 400:
                                error_body = await response.aread()
                                raise OpenAIError(
                                    f"Stream error {response.status_code}: {error_body.decode()}",
                                    status_code=response.status_code,
                                    body=error_body.decode()
                                )

                            message_id = f"openai-{uuid.uuid4().hex}"
                            finish_seen = False
                            created = int(time.time())
                            responses_normalizer = (
                                _ResponsesStreamNormalizer(
                                    message_id,
                                    model,
                                    created=created,
                                )
                                if use_responses_api
                                else None
                            )

                            # Initial role chunk
                            initial = {
                                "id": message_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"role": "assistant"},
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {_json(initial)}\n\n"

                            async for line in response.aiter_lines():
                                if not line or not line.startswith("data:"):
                                    continue
                                
                                payload = line[5:].strip()
                                if not payload or payload == "[DONE]":
                                    continue

                                try:
                                    chunk_data = json.loads(payload)
                                except json.JSONDecodeError:
                                    continue

                                if use_responses_api:
                                    if responses_normalizer is None:
                                        raise OpenAIError(
                                            "Responses stream normalizer was not initialized"
                                        )
                                    for chunk in responses_normalizer.feed(chunk_data):
                                        yield f"data: {_json(chunk)}\n\n"
                                    finish_seen = responses_normalizer.finished
                                else:
                                    # Chat Completions API streaming
                                    choices = chunk_data.get("choices", [])
                                    if not choices:
                                        continue
                                    
                                    choice = choices[0]
                                    delta = choice.get("delta", {})
                                    
                                    # Stream delta
                                    if delta:
                                        chunk = {
                                            "id": chunk_data.get("id", message_id),
                                            "object": "chat.completion.chunk",
                                            "created": chunk_data.get("created", int(time.time())),
                                            "model": model,
                                            "choices": [{
                                                "index": 0,
                                                "delta": delta,
                                                "finish_reason": choice.get("finish_reason")
                                            }]
                                        }
                                        yield f"data: {_json(chunk)}\n\n"
                                    
                                    # Check finish
                                    if choice.get("finish_reason"):
                                        finish_seen = True

                            if not finish_seen:
                                finish_reason = "stop"
                                if (
                                    responses_normalizer is not None
                                    and responses_normalizer.saw_tool_call
                                ):
                                    finish_reason = "tool_calls"
                                finish_chunk = {
                                    "id": message_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": finish_reason
                                    }]
                                }
                                yield f"data: {_json(finish_chunk)}\n\n"
                            
                            yield "data: [DONE]\n\n"
                            return

                except httpx.RequestError as e:
                    if attempt < self._max_retries:
                        logger.warning(f"Stream connection error: {e}. Retrying...")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise OpenAIError(f"Stream failed after {self._max_retries} retries: {str(e)}")

            raise OpenAIError("Max retries exceeded for streaming")

        return _stream_generator()


# --- Usage Examples ---
if __name__ == "__main__":
    async def example_gpt4_standard():
        """GPT-4: Standard chat completion."""
        client = OpenAIClient()
        
        response = await client.chat_completion(
            messages=[
                {"role": "user", "content": "Write a Python function to reverse a string."}
            ],
            model="gpt-4o",
            temperature=0.7,
            max_tokens=512
        )
        
        msg = response["choices"][0]["message"]
        print(f"💬 Response: {msg['content'][:200]}...")

    async def example_o3_reasoning():
        """o3: Advanced reasoning with Responses API."""
        client = OpenAIClient()
        
        response = await client.chat_completion(
            messages=[
                {"role": "user", "content": "Solve: x^2 + 5x + 6 = 0"}
            ],
            model="o3-mini",
            reasoning_effort="high",
            reasoning_summary="detailed",
            max_tokens=2048
        )
        
        msg = response["choices"][0]["message"]
        print(f"💭 Reasoning: {msg.get('reasoning_content', 'N/A')}")
        print(f"💬 Answer: {msg['content']}")
        print(f"🔑 Item ID: {msg.get('provider_specific', {}).get('reasoning_item_id', 'N/A')}")

    async def example_gpt5_minimal_effort():
        """GPT-5: Minimal reasoning effort (fastest)."""
        client = OpenAIClient()
        
        response = await client.chat_completion(
            messages=[
                {"role": "user", "content": "What's 2+2?"}
            ],
            model="gpt-5-mini",
            reasoning_effort="minimal",  # GPT-5 only
            max_tokens=100
        )
        
        msg = response["choices"][0]["message"]
        print(f"💬 Fast answer: {msg['content']}")

    async def example_streaming():
        """Streaming with o4-mini."""
        client = OpenAIClient()
        
        print("Starting stream...\n")
        
        stream = await client.chat_completion_stream(
            messages=[
                {"role": "user", "content": "Explain TCP/IP in one sentence."}
            ],
            model="o4-mini",
            reasoning_effort="low",
            max_tokens=512
        )
        
        async for chunk in stream:
            if chunk.startswith("data: "):
                data_str = chunk[6:].strip()
                if data_str and data_str != "[DONE]":
                    try:
                        data = json.loads(data_str)
                        delta = data["choices"][0]["delta"]
                        
                        if "content" in delta:
                            print(delta["content"], end="", flush=True)
                        elif "reasoning_content" in delta:
                            print(f"\n[Reasoning: {delta['reasoning_content']}]", end="", flush=True)
                    except json.JSONDecodeError:
                        pass
        
        print("\n\nStream complete!")

    # Run examples
    # asyncio.run(example_gpt4_standard())
    # asyncio.run(example_o3_reasoning())
    # asyncio.run(example_gpt5_minimal_effort())
    # asyncio.run(example_streaming())
