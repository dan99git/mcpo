"""Focused serialization contracts for in-memory chat sessions."""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from mcpo.services.chat_sessions import ChatSession, ChatSessionManager


@pytest.mark.parametrize(
    ("server_allowlist", "expected"),
    [
        (None, None),
        ([], []),
        (["alpha"], ["alpha"]),
    ],
)
def test_to_dict_preserves_server_allowlist_semantics(
    server_allowlist: Optional[list[str]],
    expected: Optional[list[str]],
) -> None:
    session = ChatSession(
        id="session-id",
        model="test-model",
        system_prompt=None,
        server_allowlist=server_allowlist,
    )

    assert session.to_dict()["serverAllowlist"] == expected


def test_create_session_preserves_explicit_empty_server_allowlist() -> None:
    manager = ChatSessionManager()

    session = asyncio.run(
        manager.create_session(
            model="test-model",
            server_allowlist=[],
        )
    )

    assert session.server_allowlist == []
    assert session.to_dict()["serverAllowlist"] == []
