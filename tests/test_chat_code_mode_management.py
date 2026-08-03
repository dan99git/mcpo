"""Code Mode contracts for explicitly opted-in MCPO management tools."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request

from mcpo.api.routers.chat import _execute_tool, _gather_tool_catalog
from mcpo.services.chat_sessions import ChatSession
from mcpo.services.state import StateManager


def _management_host(calls: list[str | None]) -> FastAPI:
    management = FastAPI()

    @management.post("/reload", operation_id="mcpo_reload_config")
    async def reload_config(request: Request) -> dict[str, Any]:
        calls.append(request.headers.get("authorization"))
        return {"ok": True, "reloaded": True}

    app = FastAPI()
    app.mount("/mcpo", management)
    return app


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "headers": [(b"authorization", b"Bearer harness-secret")],
        }
    )


def _code_mode_session(
    tmp_path,
    *,
    include_management_tools: bool,
) -> tuple[ChatSession, AsyncMock, list[str | None]]:
    calls: list[str | None] = []
    app = _management_host(calls)
    state = StateManager(str(tmp_path / "code-mode-state.json"))
    state.set_code_mode_enabled(True)

    with patch(
        "mcpo.api.routers.chat.collect_enabled_mcp_sessions_with_names",
        return_value=[],
    ), patch(
        "mcpo.services.state.get_state_manager",
        return_value=state,
    ):
        tool_definitions, tool_index = asyncio.run(
            _gather_tool_catalog(
                _request(app),
                allowlist=[],
                include_management_tools=include_management_tools,
            )
        )

    assert {
        tool["function"]["name"] for tool in tool_definitions
    } == {"search_tools", "execute_tool"}
    runner = AsyncMock()
    session = ChatSession(
        id="session-id",
        model="test-model",
        system_prompt=None,
        tool_definitions=tool_definitions,
        tool_index=tool_index,
        include_management_tools=include_management_tools,
    )
    return session, runner, calls


def _call_meta_tool(
    session: ChatSession,
    runner: AsyncMock,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return asyncio.run(
        _execute_tool(
            session,
            runner,
            {
                "id": f"call-{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            },
            30,
            600,
        )
    )


def test_code_mode_management_opt_in_is_searchable(tmp_path) -> None:
    session, runner, calls = _code_mode_session(
        tmp_path,
        include_management_tools=True,
    )

    result = _call_meta_tool(
        session,
        runner,
        "search_tools",
        {"query": "reload"},
    )

    assert result["ok"] is True
    assert [tool["tool"] for tool in result["output"]["tools"]] == [
        "mcpo.reload_config"
    ]
    assert result["output"]["total_available"] == 1
    runner.execute_tool.assert_not_awaited()
    assert calls == []


def test_code_mode_management_opt_in_executes_mounted_route(tmp_path) -> None:
    session, runner, calls = _code_mode_session(
        tmp_path,
        include_management_tools=True,
    )

    result = _call_meta_tool(
        session,
        runner,
        "execute_tool",
        {"tool": "mcpo.reload_config", "arguments": {}},
    )

    assert result == {
        "ok": True,
        "output": {"ok": True, "reloaded": True},
        "server": "mcpo",
        "tool": "mcpo.reload_config",
    }
    runner.execute_tool.assert_not_awaited()
    assert calls == ["Bearer harness-secret"]


def test_code_mode_management_opt_out_is_not_searchable_or_executable(
    tmp_path,
) -> None:
    session, runner, calls = _code_mode_session(
        tmp_path,
        include_management_tools=False,
    )

    search_result = _call_meta_tool(
        session,
        runner,
        "search_tools",
        {"query": "reload"},
    )
    execute_result = _call_meta_tool(
        session,
        runner,
        "execute_tool",
        {"tool": "mcpo.reload_config", "arguments": {}},
    )

    assert search_result["output"] == {"tools": [], "total_available": 0}
    assert execute_result["ok"] is False
    assert "not found" in execute_result["error"].lower()
    runner.execute_tool.assert_not_awaited()
    assert calls == []
