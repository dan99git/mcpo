from __future__ import annotations

import json

import pytest

from mcpo.middleware.request_limits import PackageArchiveBodyLimitMiddleware


class _RecordingApp:
    def __init__(self) -> None:
        self.called = False
        self.body = b""

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            self.body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _run_request(
    middleware,
    *,
    path: str,
    messages: list[dict],
    content_length: int | None = None,
) -> list[dict]:
    queued = list(messages)
    sent: list[dict] = []
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }

    async def receive():
        if queued:
            return queued.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_archive_limit_rejects_content_length_before_app() -> None:
    app = _RecordingApp()
    middleware = PackageArchiveBodyLimitMiddleware(app, max_bytes=5)

    sent = await _run_request(
        middleware,
        path="/_meta/tool-manifests/preview",
        messages=[],
        content_length=6,
    )

    assert app.called is False
    assert sent[0]["status"] == 413
    body = json.loads(sent[1]["body"])
    assert body["error"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_archive_limit_rejects_chunked_body_before_json_parsing() -> None:
    app = _RecordingApp()
    middleware = PackageArchiveBodyLimitMiddleware(app, max_bytes=5)

    sent = await _run_request(
        middleware,
        path="/_meta/skill-packages/inspect",
        messages=[
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ],
    )

    assert app.called is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_archive_limit_replays_allowed_body_and_ignores_other_routes() -> None:
    limited_app = _RecordingApp()
    limited = PackageArchiveBodyLimitMiddleware(limited_app, max_bytes=6)
    sent = await _run_request(
        limited,
        path="/prefix/_meta/skill-packages/install",
        messages=[
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ],
    )
    assert sent[0]["status"] == 204
    assert limited_app.body == b"123456"

    other_app = _RecordingApp()
    other = PackageArchiveBodyLimitMiddleware(other_app, max_bytes=2)
    sent = await _run_request(
        other,
        path="/_meta/config/save",
        messages=[
            {"type": "http.request", "body": b"unlimited", "more_body": False},
        ],
    )
    assert sent[0]["status"] == 204
    assert other_app.body == b"unlimited"
