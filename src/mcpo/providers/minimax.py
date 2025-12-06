"""
MiniMax AI Provider

Supports MiniMax models via their official API at platform.minimax.io.
Implements interleaved thinking format with <think>...</think> blocks.

Available Models:
- MiniMax-M2: Latest model, 204,800 token context, agent-native
- MiniMax-M1: 456B parameter hybrid MoE, reasoning-intensive
- MiniMax-Text-01: Text generation model

API Documentation: https://platform.minimax.io/docs/api-reference/text-post
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

import httpx

logger = logging.getLogger(__name__)


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax API returns an error payload."""


# MiniMax model definitions
MINIMAX_MODELS = [
    {"id": "minimax/MiniMax-M2", "label": "MiniMax M2 (204K context)"},
    {"id": "minimax/MiniMax-M1", "label": "MiniMax M1 (456B MoE)"},
    {"id": "minimax/MiniMax-Text-01", "label": "MiniMax Text 01"},
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
    Async client for the MiniMax chat completions API.
    
    Supports interleaved thinking format with <think>...</think> blocks
    and reasoning_details field for multi-turn reasoning continuity.
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
            base_url or 
            os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
        ).rstrip("/")
        self._timeout = timeout or 120.0  # MiniMax can be slower for reasoning

    def _default_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

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
    ) -> Dict[str, Any]:
        """
        Non-streaming chat completion.
        
        MiniMax returns reasoning in:
        - reasoning_details field (array of thinking segments)
        - <think>...</think> tags in content
        """
        # Convert model ID (remove minimax/ prefix if present)
        actual_model = get_minimax_model_name(model)
        
        payload: Dict[str, Any] = {
            "model": actual_model,
            "messages": list(messages),
        }
        
        if tools:
            payload["tools"] = list(tools)
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            url = f"{self._base_url}/chat/completions"
            logger.info(f"[MINIMAX] Non-stream request: model={actual_model}, url={url}")
            logger.info(f"[MINIMAX] Payload messages: {len(payload.get('messages', []))}, tools: {len(payload.get('tools', []) or [])}")
            logger.debug(f"[MINIMAX] Full payload: {json.dumps(payload, default=str)[:3000]}")
            
            response = await client.post(
                url,
                headers=self._default_headers(),
                content=json.dumps(payload),
            )
            logger.info(f"[MINIMAX] Response status: {response.status_code}")
            if response.status_code >= 400:
                logger.error(f"[MINIMAX] Error response: {response.text[:2000]}")
            await _raise_for_response(response)
            data = response.json()
            logger.info(f"[MINIMAX] Success: id={data.get('id')}, model={data.get('model')}")
            return data

    async def chat_completion_stream(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion.
        
        Yields SSE lines in OpenAI-compatible format.
        """
        actual_model = get_minimax_model_name(model)
        
        payload: Dict[str, Any] = {
            "model": actual_model,
            "messages": list(messages),
            "stream": True,
        }
        
        if tools:
            payload["tools"] = list(tools)
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens

        async def _iter() -> AsyncIterator[str]:
            async with httpx.AsyncClient(timeout=None) as client:
                url = f"{self._base_url}/chat/completions"
                logger.info(f"[MINIMAX] Stream request: model={actual_model}, url={url}")
                logger.info(f"[MINIMAX] Stream payload messages: {len(payload.get('messages', []))}, tools: {len(payload.get('tools', []) or [])}")
                logger.debug(f"[MINIMAX] Stream payload: {json.dumps(payload, default=str)[:3000]}")
                
                async with client.stream(
                    "POST",
                    url,
                    headers=self._default_headers(),
                    content=json.dumps(payload),
                ) as response:
                    logger.info(f"[MINIMAX] Stream response status: {response.status_code}")
                    if response.status_code >= 400:
                        error_body = await response.aread()
                        logger.error(f"[MINIMAX] Stream error: {error_body.decode()[:2000]}")
                    await _raise_for_stream_response(response)
                    chunk_count = 0
                    async for line in response.aiter_lines():
                        if line:
                            chunk_count += 1
                            if chunk_count <= 5:
                                logger.debug(f"[MINIMAX] Stream chunk {chunk_count}: {line[:300]}")
                            yield line
                    logger.info(f"[MINIMAX] Stream complete: {chunk_count} chunks")

        return _iter()


async def _raise_for_response(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail: str
        try:
            payload = response.json()
            if isinstance(payload, dict):
                # MiniMax error format
                if payload.get("base_resp"):
                    base_resp = payload["base_resp"]
                    detail = f"[{base_resp.get('status_code', 'unknown')}] {base_resp.get('status_msg', 'Unknown error')}"
                elif payload.get("error"):
                    detail = json.dumps(payload["error"])
                else:
                    detail = response.text
            else:
                detail = response.text
        except json.JSONDecodeError:
            detail = response.text
        raise MiniMaxError(detail) from exc


async def _raise_for_stream_response(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = f"MiniMax streaming request failed with status {response.status_code}"
    content_bytes: bytes = b""
    try:
        content_bytes = await response.aread()
    except RuntimeError:
        raise MiniMaxError(detail)
    except Exception as exc:
        raise MiniMaxError(f"{detail}: {exc}") from exc

    if content_bytes:
        try:
            payload = json.loads(content_bytes.decode() or "{}")
            if isinstance(payload, dict):
                if payload.get("base_resp"):
                    base_resp = payload["base_resp"]
                    detail = f"[{base_resp.get('status_code', 'unknown')}] {base_resp.get('status_msg', 'Unknown error')}"
                elif payload.get("error"):
                    detail = json.dumps(payload["error"])
            elif isinstance(payload, str):
                detail = payload
            else:
                detail = content_bytes.decode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            decoded = content_bytes.decode(errors="replace").strip()
            if decoded:
                detail = decoded

    raise MiniMaxError(detail)
