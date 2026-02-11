"""
MiniMax AI Provider — Anthropic Coding Plan Endpoint

Routes MiniMax models through MiniMax's Anthropic-compatible API at
https://api.minimax.io/anthropic (the officially RECOMMENDED endpoint).

This is a thin wrapper around AnthropicClient with MiniMax-specific
base URL and API key.  All heavy lifting (message mapping, thinking
blocks, tool_use, streaming, retries) is handled by the Anthropic
provider.

Available Models (via Anthropic Coding Plan endpoint):
- MiniMax-M2.1        : Polyglot programming, ~60 tps
- MiniMax-M2.1-lightning : Faster and more agile, ~100 tps
- MiniMax-M2          : Agentic capabilities, advanced reasoning

API Documentation:
  https://platform.minimax.io/docs/api-reference/text-anthropic-api
Coding Plan:
  https://platform.minimax.io/subscribe/coding-plan
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

from mcpo.providers.anthropic import AnthropicClient, AnthropicError

logger = logging.getLogger(__name__)

# Default base URL — MiniMax Anthropic-compatible (RECOMMENDED by MiniMax)
MINIMAX_ANTHROPIC_BASE_URL = "https://api.minimax.io/anthropic"


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax API returns an error payload."""


# MiniMax model definitions (Anthropic Coding Plan endpoint)
MINIMAX_MODELS = [
    {"id": "minimax/MiniMax-M2.1", "label": "MiniMax M2.1 (Coding Plan, ~60 tps)"},
    {"id": "minimax/MiniMax-M2.1-lightning", "label": "MiniMax M2.1 Lightning (~100 tps)"},
    {"id": "minimax/MiniMax-M2", "label": "MiniMax M2 (Agentic, Advanced Reasoning)"},
]


def get_minimax_models() -> List[Dict[str, str]]:
    """Return list of available MiniMax models."""
    return MINIMAX_MODELS.copy()


def is_minimax_model(model_id: str) -> bool:
    """Check if model ID is a MiniMax model."""
    return model_id.startswith("minimax/")


def get_minimax_model_name(model_id: str) -> str:
    """Extract the actual model name from prefixed ID."""
    if model_id.startswith("minimax/"):
        return model_id[8:]  # Remove "minimax/" prefix
    return model_id


class MiniMaxClient:
    """
    Async client for MiniMax via the Anthropic-compatible Coding Plan API.

    Delegates to AnthropicClient with:
      base_url = https://api.minimax.io/anthropic
      api_key  = MINIMAX_API_KEY

    Supports thinking blocks, tool_use, streaming — all handled by the
    Anthropic provider's message mapping and SSE translation.

    The external interface (chat_completion / chat_completion_stream with
    max_output_tokens) is preserved so callers in chat.py need zero changes.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._api_key = api_key or os.getenv("MINIMAX_API_KEY")
        if not self._api_key:
            raise MiniMaxError("MINIMAX_API_KEY environment variable is required")

        self._base_url = (
            base_url
            or os.getenv("MINIMAX_BASE_URL", MINIMAX_ANTHROPIC_BASE_URL)
        ).rstrip("/")
        self._timeout = timeout or 120.0

        # Instantiate the underlying Anthropic client pointed at MiniMax.
        # Prompt caching is disabled — MiniMax may not support Anthropic
        # cache_control headers.
        try:
            self._anthropic = AnthropicClient(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
                enable_prompt_caching=False,
            )
        except AnthropicError as exc:
            raise MiniMaxError(str(exc)) from exc

    async def chat_completion(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        include_reasoning: bool = True,
        thinking_budget: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Non-streaming chat completion via MiniMax Anthropic endpoint.

        Accepts the same kwargs as the old OpenAI-compat client so
        callers (chat.py) do not need changes.  The AnthropicClient
        returns an OpenAI-compatible dict (object="chat.completion").
        """
        actual_model = get_minimax_model_name(model)
        max_tokens = max_output_tokens or 4096

        logger.info(
            f"[MINIMAX] Non-stream via Anthropic endpoint: "
            f"model={actual_model}, base_url={self._base_url}"
        )

        try:
            return await self._anthropic.chat_completion(
                messages=messages,
                model=actual_model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_budget=thinking_budget,
            )
        except AnthropicError as exc:
            raise MiniMaxError(str(exc)) from exc

    async def chat_completion_stream(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion via MiniMax Anthropic endpoint.

        Yields SSE lines already translated to OpenAI-compatible format
        by the Anthropic provider.
        """
        actual_model = get_minimax_model_name(model)
        max_tokens = max_output_tokens or 4096

        logger.info(
            f"[MINIMAX] Stream via Anthropic endpoint: "
            f"model={actual_model}, base_url={self._base_url}"
        )

        try:
            return await self._anthropic.chat_completion_stream(
                messages=messages,
                model=actual_model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_budget=thinking_budget,
            )
        except AnthropicError as exc:
            raise MiniMaxError(str(exc)) from exc
