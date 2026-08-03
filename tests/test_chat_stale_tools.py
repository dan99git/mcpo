"""Chat sessions must not send disabled servers'/tools' definitions to providers.

Covers the stale-cache leak: tool definitions gathered at session creation must
be revalidated on every message so that disabling a server (or a single tool)
takes effect immediately, and re-enabling restores the tools.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import mcpo.services.state as _state_mod
from mcpo.api.routers.chat import (
    ChatMessageRequest,
    _call_provider,
    _ensure_tools,
    _gather_tool_catalog,
    _tool_catalog_state_signature,
)
from mcpo.services.chat_sessions import ChatSessionManager


class FakeTool:
    def __init__(self, name: str):
        self.name = name
        self.description = f"tool {name}"
        self.input_schema = {"type": "object", "properties": {}}


class FakeMCPSession:
    def __init__(self, tool_names):
        self._tool_names = tool_names

    async def list_tools(self):
        return SimpleNamespace(tools=[FakeTool(name) for name in self._tool_names])


class FakeChatClient:
    """Captures the tools list the provider call receives."""

    def __init__(self):
        self.calls = []

    async def chat_completion(self, messages, model, tools, temperature):
        self.calls.append({"model": model, "tools": tools})
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }


@pytest.fixture
def state_manager(tmp_path, monkeypatch):
    state = _state_mod.StateManager(str(tmp_path / "state.json"))
    monkeypatch.setattr(_state_mod, "_global_state_manager", state)
    return state


def _build_app() -> FastAPI:
    app = FastAPI()
    for server, tools in (("alpha", ["a_one", "a_two"]), ("beta", ["b_one"])):
        sub = FastAPI(title=server)
        sub.state.session = FakeMCPSession(tools)
        sub.state.is_connected = True
        app.mount(f"/{server}", sub)
    return app


def _fake_request(app: FastAPI):
    return SimpleNamespace(app=app, headers={})


def _tool_names(session) -> set:
    return {d["function"]["name"] for d in session.tool_definitions}


async def _create_session(request):
    """Mirror the create_session route: signature + gathered catalog."""
    signature = _tool_catalog_state_signature()
    tool_defs, tool_index = await _gather_tool_catalog(request)
    manager = ChatSessionManager()
    return await manager.create_session(
        model="test/model",
        tool_definitions=tool_defs,
        tool_index=tool_index,
        tools_state_signature=signature,
    )


@pytest.mark.asyncio
async def test_disable_server_drops_tools_from_next_provider_call(state_manager):
    request = _fake_request(_build_app())
    session = await _create_session(request)
    assert _tool_names(session) == {"alpha_a_one", "alpha_a_two", "beta_b_one"}

    # Unchanged state: cached definitions are reused, not regathered.
    cached = session.tool_definitions
    await _ensure_tools(session, request)
    assert session.tool_definitions is cached

    state_manager.set_server_enabled("beta", False)

    await _ensure_tools(session, request)
    assert _tool_names(session) == {"alpha_a_one", "alpha_a_two"}
    assert set(session.tool_index.keys()) == {"alpha_a_one", "alpha_a_two"}

    # The provider call must receive only the enabled server's tools.
    client = FakeChatClient()
    payload = ChatMessageRequest(message="hi", stream=False)
    await _call_provider(session, client, payload, stream=False, emitter=None)
    assert len(client.calls) == 1
    sent = {t["function"]["name"] for t in client.calls[0]["tools"]}
    assert sent == {"alpha_a_one", "alpha_a_two"}


@pytest.mark.asyncio
async def test_disable_single_tool_drops_only_that_tool(state_manager):
    request = _fake_request(_build_app())
    session = await _create_session(request)
    assert "alpha_a_two" in _tool_names(session)

    state_manager.set_tool_enabled("alpha", "a_two", False)

    await _ensure_tools(session, request)
    assert _tool_names(session) == {"alpha_a_one", "beta_b_one"}
    assert set(session.tool_index.keys()) == {"alpha_a_one", "beta_b_one"}


@pytest.mark.asyncio
async def test_cross_process_disable_is_seen(state_manager, tmp_path):
    """A disable written to the state file by another process must be picked up."""
    request = _fake_request(_build_app())
    session = await _create_session(request)
    assert "beta_b_one" in _tool_names(session)

    # Simulate another process writing to the same state file.
    other = _state_mod.StateManager(str(tmp_path / "state.json"))
    other.set_server_enabled("beta", False)

    await _ensure_tools(session, request)
    assert _tool_names(session) == {"alpha_a_one", "alpha_a_two"}


@pytest.mark.asyncio
async def test_reenable_restores_tools(state_manager):
    request = _fake_request(_build_app())
    session = await _create_session(request)

    state_manager.set_server_enabled("beta", False)
    await _ensure_tools(session, request)
    assert _tool_names(session) == {"alpha_a_one", "alpha_a_two"}

    state_manager.set_server_enabled("beta", True)
    await _ensure_tools(session, request)
    assert _tool_names(session) == {"alpha_a_one", "alpha_a_two", "beta_b_one"}
