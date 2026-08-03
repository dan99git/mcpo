"""Execution-time revocation contracts for cached chat tool catalogs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcpo.api.routers.chat import _execute_tool
from mcpo.services.chat_sessions import ChatSession
from mcpo.services.state import StateManager


def _cached_session(*, code_mode: bool) -> tuple[ChatSession, AsyncMock, object]:
    mcp_session = object()
    tool = SimpleNamespace(name="dangerous_tool")
    cached_mapping = {
        "server": "alpha",
        "session": mcp_session,
        "tool": tool,
        "originalName": "alpha.dangerous_tool",
    }
    if code_mode:
        tool_index = {
            "execute_tool": {
                "server": "__code_mode__",
                "is_code_mode_tool": True,
                "code_catalog": [],
                "original_tool_index": {
                    "alpha_dangerous_tool": cached_mapping,
                },
            }
        }
    else:
        tool_index = {"alpha_dangerous_tool": cached_mapping}

    session = ChatSession(
        id="session-id",
        model="test-model",
        system_prompt=None,
        tool_definitions=[{"type": "function"}],
        tool_index=tool_index,
        server_allowlist=["alpha"],
    )
    runner = AsyncMock()
    runner.execute_tool.return_value = {"ran": True}
    return session, runner, mcp_session


def _execute_cached(
    session: ChatSession,
    runner: AsyncMock,
    *,
    code_mode: bool,
) -> dict:
    if code_mode:
        name = "execute_tool"
        arguments = {
            "tool": "alpha.dangerous_tool",
            "arguments": {"value": 1},
        }
    else:
        name = "alpha_dangerous_tool"
        arguments = {"value": 1}
    return asyncio.run(
        _execute_tool(
            session,
            runner,
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            },
            30,
            600,
        )
    )


@pytest.mark.parametrize("code_mode", [False, True], ids=["direct", "code-mode"])
@pytest.mark.parametrize("revoked", ["server", "tool"])
def test_revoked_state_blocks_cached_tool_execution(
    tmp_path,
    code_mode: bool,
    revoked: str,
) -> None:
    state = StateManager(str(tmp_path / f"{code_mode}-{revoked}.json"))
    state.set_server_enabled("alpha", revoked != "server")
    state.set_tool_enabled("alpha", "dangerous_tool", revoked != "tool")
    session, runner, _mcp_session = _cached_session(code_mode=code_mode)

    with patch("mcpo.services.state.get_state_manager", return_value=state):
        result = _execute_cached(session, runner, code_mode=code_mode)

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()
    runner.execute_tool.assert_not_awaited()


@pytest.mark.parametrize("code_mode", [False, True], ids=["direct", "code-mode"])
def test_enabled_cached_tool_remains_executable(tmp_path, code_mode: bool) -> None:
    state = StateManager(str(tmp_path / f"enabled-{code_mode}.json"))
    state.set_server_enabled("alpha", True)
    state.set_tool_enabled("alpha", "dangerous_tool", True)
    session, runner, mcp_session = _cached_session(code_mode=code_mode)

    with patch("mcpo.services.state.get_state_manager", return_value=state):
        result = _execute_cached(session, runner, code_mode=code_mode)

    assert result == {
        "ok": True,
        "output": {"ran": True},
        "server": "alpha",
        "tool": "dangerous_tool",
    }
    runner.execute_tool.assert_awaited_once_with(
        mcp_session,
        "dangerous_tool",
        {"value": 1},
        timeout=30,
        max_timeout=600,
    )


@pytest.mark.parametrize("code_mode", [False, True], ids=["direct", "code-mode"])
def test_explicit_empty_allowlist_blocks_cached_tool_execution(
    tmp_path,
    code_mode: bool,
) -> None:
    state = StateManager(str(tmp_path / f"allowlist-{code_mode}.json"))
    state.set_server_enabled("alpha", True)
    state.set_tool_enabled("alpha", "dangerous_tool", True)
    session, runner, _mcp_session = _cached_session(code_mode=code_mode)
    session.server_allowlist = []

    with patch("mcpo.services.state.get_state_manager", return_value=state):
        result = _execute_cached(session, runner, code_mode=code_mode)

    assert result["ok"] is False
    assert "allowlist" in result["error"].lower()
    runner.execute_tool.assert_not_awaited()
