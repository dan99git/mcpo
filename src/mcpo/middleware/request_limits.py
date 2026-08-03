from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from mcpo.services.skill_packages import MAX_ARCHIVE_BASE64_CHARS


MAX_ARCHIVE_REQUEST_BYTES = MAX_ARCHIVE_BASE64_CHARS + 4_096
MAX_CHAT_REQUEST_BYTES = 30 * 1024 * 1024
_LIMITED_PATH_SUFFIXES = (
    "/_meta/skill-packages/inspect",
    "/_meta/skill-packages/install",
    "/_meta/tool-manifests/preview",
)


class PackageArchiveBodyLimitMiddleware:
    """Bound archive-management requests before JSON and base64 parsing."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[Any]],
        max_bytes: int = MAX_ARCHIVE_REQUEST_BYTES,
    ) -> None:
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        self.app = app
        self.max_bytes = max_bytes

    @staticmethod
    def _is_limited(scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        path = str(scope.get("path") or "")
        return path.endswith(_LIMITED_PATH_SUFFIXES)

    def _declared_too_large(self, scope: Scope) -> bool:
        for name, value in scope.get("headers") or []:
            if name.lower() != b"content-length":
                continue
            try:
                return int(value) > self.max_bytes
            except (TypeError, ValueError):
                return False
        return False

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "ok": False,
                "error": {
                    "message": "Archive request body is too large",
                    "code": "request_too_large",
                },
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_limited(scope):
            await self.app(scope, receive, send)
            return
        if self._declared_too_large(scope):
            await self._reject(scope, receive, send)
            return

        buffered: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)


class ChatBodyLimitMiddleware(PackageArchiveBodyLimitMiddleware):
    """Bound base64 chat uploads before JSON and attachment decoding."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[Any]],
        max_bytes: int = MAX_CHAT_REQUEST_BYTES,
    ) -> None:
        super().__init__(app, max_bytes=max_bytes)

    @staticmethod
    def _is_limited(scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        path = str(scope.get("path") or "")
        return "/chat/sessions/" in path and path.endswith("/messages")

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "ok": False,
                "error": {
                    "message": "Chat request body is too large",
                    "code": "request_too_large",
                },
            },
        )
        await response(scope, receive, send)


__all__ = [
    "MAX_ARCHIVE_REQUEST_BYTES",
    "MAX_CHAT_REQUEST_BYTES",
    "ChatBodyLimitMiddleware",
    "PackageArchiveBodyLimitMiddleware",
]
