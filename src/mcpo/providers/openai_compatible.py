from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, Iterable, Optional

import httpx


class OpenAICompatibleError(RuntimeError):
    """Raised when an OpenAI-compatible provider rejects a request."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class OpenAICompatibleClient:
    """Small chat client for user-configured OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        stream_timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._stream_timeout = stream_timeout

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]],
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        stream: bool,
        reasoning_effort: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": stream,
        }
        if tools:
            payload["tools"] = list(tools)
        if temperature is not None:
            payload["temperature"] = temperature
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        return payload

    @staticmethod
    async def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body: Any = response.json()
        except Exception:
            body = response.text
        message = f"OpenAI-compatible provider returned HTTP {response.status_code}"
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            elif isinstance(error, str):
                message = error
            elif body.get("message"):
                message = str(body["message"])
        elif body:
            message = str(body)[:1000]
        raise OpenAICompatibleError(message, response.status_code, body)

    async def chat_completion(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        payload = self._payload(
            messages=messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            stream=False,
            reasoning_effort=reasoning_effort,
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                content=json.dumps(payload),
            )
        await self._raise_for_response(response)
        data = response.json()
        if not isinstance(data, dict):
            raise OpenAICompatibleError("Provider returned a non-object completion response.")
        return data

    async def chat_completion_stream(
        self,
        *,
        messages: Iterable[Dict[str, Any]],
        model: str,
        tools: Optional[Iterable[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        payload = self._payload(
            messages=messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            stream=True,
            reasoning_effort=reasoning_effort,
        )
        timeout = httpx.Timeout(self._stream_timeout, connect=min(self._timeout, 30.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                content=json.dumps(payload),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    await self._raise_for_response(response)
                async for line in response.aiter_lines():
                    if line:
                        yield line if line.startswith("data:") else f"data: {line}"
