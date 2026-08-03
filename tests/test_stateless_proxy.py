"""Tests for roots suppression, proxy era propagation, and admin remount flags."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import mcp.types
import pytest
import shutil
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client, Context, FastMCP
from fastmcp.mcp_config import MCPConfig
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
    StatefulProxyClient,
)

from mcpo.proxy import (
    _BridgedProxyClient,
    _BridgedStatefulProxyClient,
    _EraAwareProxyClientFactory,
    _proxy_no_roots_forward,
)


def _test_cfg() -> MCPConfig:
    return MCPConfig.from_dict(
        {"mcpServers": {"echo": {"command": "echo", "args": ["hi"]}}}
    )


@pytest.mark.asyncio
async def test_stateless_factory_suppresses_roots_and_negotiates_backend_independently() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    assert isinstance(proxy, FastMCPProxy)
    assert proxy.provider_error_strategy == "raise"
    assert isinstance(proxy.client_factory, _EraAwareProxyClientFactory)
    client = proxy.client_factory()
    assert isinstance(client, ProxyClient)
    assert client.mode == "auto"
    assert client._transport_options.backend_mode == "auto"

    callback = client._session_kwargs["list_roots_callback"]
    result = await callback(None)
    assert isinstance(result, mcp.types.ListRootsResult)
    assert result.roots == []


@pytest.mark.asyncio
async def test_stateless_factory_returns_fresh_clients_for_each_request() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    first = proxy.client_factory()
    second = proxy.client_factory()

    assert first is not second
    assert first.mode == second.mode == "auto"
    assert first._transport_options.backend_mode == "auto"
    for client in (first, second):
        result = await client._session_kwargs["list_roots_callback"](None)
        assert isinstance(result, mcp.types.ListRootsResult)
        assert result.roots == []


def test_single_backend_without_request_context_uses_auto() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    client = proxy.client_factory()

    assert isinstance(client, ProxyClient)
    assert client.mode == "auto"


@pytest.mark.asyncio
async def test_single_backend_stateful_factory_normalizes_every_front_to_auto() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg())
    factory = proxy.client_factory

    assert isinstance(factory, _EraAwareProxyClientFactory)
    modern = factory.client_for_mode("2026-07-28")
    legacy = factory.client_for_mode("legacy")
    assert isinstance(modern, StatefulProxyClient)
    assert modern is legacy
    assert modern.mode == "auto"
    assert modern._transport_options.backend_mode == "auto"
    result = await modern._session_kwargs["list_roots_callback"](None)
    assert isinstance(result, mcp.types.ListRootsResult)
    assert result.roots == []


@pytest.mark.parametrize(
    ("client_type", "factory_method"),
    [
        (_BridgedProxyClient, "new"),
        (_BridgedStatefulProxyClient, "new_stateful"),
    ],
)
@pytest.mark.asyncio
async def test_modern_front_bridges_strict_legacy_tool_metadata(
    client_type,
    factory_method,
) -> None:
    backend = FastMCP("strict-legacy")

    @backend.tool
    def echo(value: str, ctx: Context) -> str:
        return f"{value}:{ctx.request_context.meta['vendor']}"

    base = client_type(backend, roots=[], mode="legacy")
    proxy = FastMCPProxy(
        client_factory=getattr(base, factory_method),
        provider_error_strategy="raise",
    )

    try:
        async with Client(proxy, mode="2026-07-28") as front:
            result = await front.call_tool_mcp(
                "echo",
                {"value": "bridged"},
                meta={"vendor": "kept"},
            )

            assert result.is_error is False
            assert result.content[0].text == "bridged:kept"
    finally:
        if isinstance(base, StatefulProxyClient):
            await base.clear()


@pytest.mark.asyncio
async def test_modern_front_calls_pinned_legacy_time_vendor(tmp_path, monkeypatch) -> None:
    if shutil.which("uvx") is None:
        pytest.skip("uvx not available; cannot start the pinned time vendor")

    import mcpo.services.state as state_module

    state = state_module.StateManager(str(tmp_path / "state.json"))
    state.set_server_enabled("time", True)
    monkeypatch.setattr(state_module, "_global_state_manager", state)

    cfg = MCPConfig.from_dict(
        {
            "mcpServers": {
                "time": {
                    "command": "uvx",
                    "args": [
                        "--from",
                        "mcp-server-time==2026.7.10",
                        "--with",
                        "mcp==1.29.0",
                        "mcp-server-time",
                    ],
                }
            }
        }
    )
    proxy = _proxy_no_roots_forward(cfg, stateless=True)

    async with Client(proxy, mode="2026-07-28") as front:
        assert front.protocol_version == "2026-07-28"
        tools = await front.list_tools()
        result = await front.call_tool_mcp(
            "get_current_time",
            {"timezone": "UTC"},
        )

    assert {tool.name for tool in tools} >= {"get_current_time", "convert_time"}
    assert result.is_error is False
    assert result.content


def test_multi_backend_mirrors_front_era_but_negotiates_each_backend(monkeypatch) -> None:
    cfg = MCPConfig.from_dict(
        {
            "mcpServers": {
                "legacy": {"command": "echo", "args": ["legacy"]},
                "modern": {"command": "echo", "args": ["modern"]},
            }
        }
    )
    monkeypatch.setattr("mcpo.proxy._proxy_backend_mode", lambda: "2026-07-28")
    client = _proxy_no_roots_forward(cfg, stateless=True).client_factory()

    assert client.mode == "2026-07-28"
    assert client._transport_options.backend_mode == "auto"

@pytest.mark.asyncio
async def test_plain_create_proxy_forwards_roots() -> None:
    proxy = create_proxy(_test_cfg())
    client = proxy.client_factory()
    callback = client._session_kwargs["list_roots_callback"]
    result = await callback(None)
    assert not (
        isinstance(result, mcp.types.ListRootsResult) and result.roots == []
    )


def test_single_backend_aggregate_binds_policy_to_backend_name() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    assert [item.server_name for item in proxy.middleware[-2:]] == [
        "echo",
        "echo",
    ]

def test_multi_backend_aggregate_passes_configured_namespace_map() -> None:
    cfg = MCPConfig.from_dict(
        {
            "mcpServers": {
                "alpha": {"command": "echo", "args": ["alpha"]},
                "longserver": {"command": "echo", "args": ["longserver"]},
            }
        }
    )
    proxy = _proxy_no_roots_forward(cfg, stateless=True)

    assert [item.server_name for item in proxy.middleware[-2:]] == [None, None]
    assert [item.server_names for item in proxy.middleware[-2:]] == [
        ("longserver", "alpha"),
        ("longserver", "alpha"),
    ]



def test_proxy_registers_code_mode_before_tool_filter() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True, server_name="echo")

    assert [type(item).__name__ for item in proxy.middleware[-2:]] == [
        "CodeModeMCPMiddleware",
        "MCPToolFilterMiddleware",
    ]
    assert proxy.middleware[-1].server_name == "echo"


class _FakeRemoteProxy:
    def __init__(self, recorded: list):
        self._recorded = recorded
        self.middleware = []

    def add_middleware(self, middleware) -> None:
        self.middleware.append(middleware)

    def http_app(
        self,
        path,
        transport,
        stateless_http,
        host_origin_protection,
    ):
        self._recorded.append(
            {
                "stateless": stateless_http,
                "host_origin_protection": host_origin_protection,
                "middleware": [type(item).__name__ for item in self.middleware],
            }
        )
        return FastAPI()


def _install_fake_shared_proxy(monkeypatch, recorded: list) -> list:
    import mcpo.proxy as proxy_module

    helper_calls: list = []

    def build_proxy(cfg, auth=None, stateless=False, server_name=None):
        helper_calls.append(
            {
                "servers": tuple(cfg.mcpServers),
                "stateless": stateless,
                "server_name": server_name,
            }
        )
        return _FakeRemoteProxy(recorded)

    monkeypatch.setattr(proxy_module, "_proxy_no_roots_forward", build_proxy)
    return helper_calls


async def _run_admin_remount(monkeypatch, *, proxy_stateless):
    from mcpo.api.routers import admin as admin_module

    recorded: list = []
    helper_calls = _install_fake_shared_proxy(monkeypatch, recorded)

    main_app = FastAPI()
    main_app.state.config_data = {
        "mcpServers": {"srv1": {"command": "echo", "args": ["hi"]}}
    }
    if proxy_stateless is not None:
        main_app.state.proxy_stateless = proxy_stateless

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")
    await admin_module._shutdown_fastmcp_proxy(main_app)
    return recorded, helper_calls


@pytest.mark.asyncio
async def test_admin_remount_uses_shared_proxy_and_transport_security(monkeypatch) -> None:
    recorded, helper_calls = await _run_admin_remount(
        monkeypatch,
        proxy_stateless=True,
    )

    assert [item["stateless"] for item in recorded] == [True, True]
    assert all(item["host_origin_protection"] is True for item in recorded)
    assert all(item["middleware"] == [] for item in recorded)
    assert helper_calls == [
        {"servers": ("srv1",), "stateless": True, "server_name": None},
        {"servers": ("srv1",), "stateless": True, "server_name": "srv1"},
    ]


@pytest.mark.asyncio
async def test_admin_remount_defaults_shared_proxy_to_stateful(monkeypatch) -> None:
    recorded, helper_calls = await _run_admin_remount(
        monkeypatch,
        proxy_stateless=None,
    )
    assert [item["stateless"] for item in recorded] == [None, None]
    assert [item["stateless"] for item in helper_calls] == [False, False]


@pytest.mark.asyncio
async def test_admin_remount_prioritizes_specific_proxy_routes(monkeypatch) -> None:
    from mcpo.api.routers import admin as admin_module

    recorded: list = []
    _install_fake_shared_proxy(monkeypatch, recorded)

    main_app = FastAPI()
    static_mcp_app = FastAPI()
    main_app.mount("/mcp", static_mcp_app)
    main_app.state.config_data = {
        "mcpServers": {"srv1": {"command": "echo", "args": ["hi"]}}
    }

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    mcp_routes = [
        route
        for route in main_app.router.routes
        if getattr(route, "path", "").startswith("/mcp")
    ]
    assert [route.path for route in mcp_routes] == ["/mcp/srv1", "/mcp", "/mcp"]
    assert [
        bool(getattr(getattr(route.app, "state", None), "is_fastmcp_proxy", False))
        for route in mcp_routes
    ] == [True, True, False]

    await admin_module._shutdown_fastmcp_proxy(main_app)


@pytest.mark.asyncio
async def test_admin_remount_propagates_proxy_build_failure(monkeypatch) -> None:
    import mcpo.proxy as proxy_module
    from mcpo.api.routers import admin as admin_module

    def fail_create_proxy(*args, **kwargs):
        raise RuntimeError("proxy build failed")

    monkeypatch.setattr(proxy_module, "_proxy_no_roots_forward", fail_create_proxy)

    main_app = FastAPI()
    main_app.state.config_data = {
        "mcpServers": {"srv1": {"command": "echo", "args": ["hi"]}}
    }

    with pytest.raises(RuntimeError, match="proxy build failed"):
        await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    assert not any(
        bool(getattr(getattr(route.app, "state", None), "is_fastmcp_proxy", False))
        for route in main_app.router.routes
    )


@pytest.mark.asyncio
async def test_admin_remount_clears_proxy_without_enabled_servers(monkeypatch) -> None:
    from mcpo.api.routers import admin as admin_module

    recorded: list = []
    helper_calls = _install_fake_shared_proxy(monkeypatch, recorded)

    main_app = FastAPI()
    old_proxy = FastAPI()
    old_proxy.state.is_fastmcp_proxy = True
    main_app.mount("/mcp", old_proxy)
    main_app.state.config_data = {"mcpServers": {}}

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    assert helper_calls == []
    assert recorded == []
    assert main_app.state.fastmcp_proxy_mounts == []
    assert main_app.state.fastmcp_proxy_global_mount is None
    assert not any(
        bool(getattr(getattr(route.app, "state", None), "is_fastmcp_proxy", False))
        for route in main_app.router.routes
    )


@pytest.mark.asyncio
async def test_admin_remount_checks_origin_before_api_key(monkeypatch) -> None:
    from mcpo.api.routers import admin as admin_module

    recorded: list = []
    _install_fake_shared_proxy(monkeypatch, recorded)

    main_app = FastAPI()
    main_app.state.config_data = {
        "mcpServers": {"srv1": {"command": "echo", "args": ["hi"]}}
    }
    main_app.state.api_key = "secret"
    main_app.state.strict_auth = True

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    client = TestClient(main_app)
    hostile_origin = client.post(
        "/mcp/",
        headers={
            "Host": "host.docker.internal",
            "Origin": "https://attacker.invalid",
        },
    )
    missing_auth = client.post(
        "/mcp/",
        headers={"Host": "host.docker.internal"},
    )

    assert hostile_origin.status_code == 403
    assert missing_auth.status_code == 401

    await admin_module._shutdown_fastmcp_proxy(main_app)


@pytest.mark.asyncio
async def test_task_owned_lifespan_enters_and_exits_in_owner_task() -> None:
    from mcpo.api.routers.admin import _TaskOwnedLifespan

    observed: dict[str, asyncio.Task] = {}

    @asynccontextmanager
    async def lifespan():
        observed["enter"] = asyncio.current_task()
        try:
            yield
        finally:
            observed["exit"] = asyncio.current_task()

    handle = _TaskOwnedLifespan(lifespan(), label="task-affinity")
    caller_task = asyncio.current_task()
    await handle.__aenter__()
    await asyncio.create_task(handle.__aexit__(None, None, None))

    assert observed["enter"] is observed["exit"]
    assert observed["enter"] is not caller_task


@pytest.mark.asyncio
async def test_task_owned_lifespan_propagates_startup_failure() -> None:
    from mcpo.api.routers.admin import _TaskOwnedLifespan

    @asynccontextmanager
    async def broken_lifespan():
        raise RuntimeError("startup failed")
        yield

    handle = _TaskOwnedLifespan(broken_lifespan(), label="broken")
    with pytest.raises(RuntimeError, match="startup failed"):
        await handle.__aenter__()

    assert handle._task is not None
    assert handle._task.done()


@pytest.mark.asyncio
async def test_admin_candidate_failure_retains_exact_old_generation(monkeypatch) -> None:
    import mcpo.proxy as proxy_module
    from mcpo.api.routers import admin as admin_module

    events: list[tuple[str, asyncio.Task]] = []

    @asynccontextmanager
    async def live_lifespan(_app):
        events.append(("candidate_enter", asyncio.current_task()))
        try:
            yield
        finally:
            events.append(("candidate_exit", asyncio.current_task()))

    @asynccontextmanager
    async def broken_lifespan(_app):
        raise RuntimeError("candidate startup failed")
        yield

    class CandidateProxy:
        def __init__(self, lifespan):
            self._lifespan = lifespan

        def http_app(self, **kwargs):
            return FastAPI(lifespan=self._lifespan)

    candidates = iter(
        [
            CandidateProxy(live_lifespan),
            CandidateProxy(broken_lifespan),
        ]
    )
    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: next(candidates),
    )
    monkeypatch.setattr(
        admin_module,
        "get_state_manager",
        lambda: type(
            "EnabledState",
            (),
            {"is_server_enabled": lambda self, _name: True},
        )(),
    )
    monkeypatch.setattr(admin_module, "_FASTMCP_AVAILABLE", True)

    class OldLifespan:
        def __init__(self):
            self.exited = False

        async def __aexit__(self, exc_type, exc, traceback):
            self.exited = True

    main_app = FastAPI()
    old_app = FastAPI()
    old_app.state.is_fastmcp_proxy = True
    main_app.mount("/mcp", old_app)
    old_route = main_app.router.routes[-1]
    old_lifespan = OldLifespan()
    old_mounts = [
        {"id": "old", "label": "old", "path": "/mcp", "scope": "global"}
    ]
    old_lifespans = {"/mcp": old_lifespan}
    main_app.state.fastmcp_proxy_mounts = old_mounts
    main_app.state.fastmcp_proxy_global_mount = "/mcp"
    main_app.state.fastmcp_proxy_lifespans = old_lifespans
    main_app.state.config_data = {
        "mcpServers": {"next": {"command": "echo", "args": ["next"]}}
    }

    with pytest.raises(
        RuntimeError,
        match="Failed to start FastMCP proxy lifespan for server 'next'",
    ) as exc_info:
        await admin_module._mount_or_remount_fastmcp(
            main_app,
            base_path="/mcp",
        )

    assert str(exc_info.value.__cause__) == "candidate startup failed"
    assert main_app.router.routes[-1] is old_route
    assert main_app.state.fastmcp_proxy_mounts is old_mounts
    assert main_app.state.fastmcp_proxy_lifespans is old_lifespans
    assert main_app.state.fastmcp_proxy_global_mount == "/mcp"
    assert old_lifespan.exited is False
    assert [name for name, _task in events] == [
        "candidate_enter",
        "candidate_exit",
    ]
    assert events[0][1] is events[1][1]


@pytest.mark.asyncio
async def test_admin_successful_swap_publishes_candidate_before_retirement(
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module
    from mcpo.api.routers import admin as admin_module

    @asynccontextmanager
    async def live_lifespan(_app):
        yield

    class LiveProxy:
        def http_app(self, **kwargs):
            return FastAPI(lifespan=live_lifespan)

    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: LiveProxy(),
    )
    monkeypatch.setattr(
        admin_module,
        "get_state_manager",
        lambda: type(
            "EnabledState",
            (),
            {"is_server_enabled": lambda self, _name: True},
        )(),
    )
    monkeypatch.setattr(admin_module, "_FASTMCP_AVAILABLE", True)

    main_app = FastAPI()
    old_app = FastAPI()
    old_app.state.is_fastmcp_proxy = True
    main_app.mount("/mcp", old_app)
    old_route = main_app.router.routes[-1]

    class OldLifespan:
        def __init__(self):
            self.exited = False

        async def __aexit__(self, exc_type, exc, traceback):
            native_paths = [
                route.path
                for route in main_app.router.routes
                if bool(
                    getattr(
                        getattr(getattr(route, "app", None), "state", None),
                        "is_fastmcp_proxy",
                        False,
                    )
                )
            ]
            assert old_route not in main_app.router.routes
            assert native_paths == ["/mcp/next", "/mcp"]
            self.exited = True

    old_lifespan = OldLifespan()
    main_app.state.fastmcp_proxy_lifespans = {"/mcp": old_lifespan}
    main_app.state.config_data = {
        "mcpServers": {"next": {"command": "echo", "args": ["next"]}}
    }

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    assert old_lifespan.exited is True
    assert set(main_app.state.fastmcp_proxy_lifespans) == {
        "/mcp",
        "/mcp/next",
    }
    assert all(
        isinstance(handle, admin_module._TaskOwnedLifespan)
        for handle in main_app.state.fastmcp_proxy_lifespans.values()
    )

    await admin_module._shutdown_fastmcp_proxy(main_app)
    assert not any(
        bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
        for route in main_app.router.routes
    )
    with pytest.raises(RuntimeError, match="closed during shutdown"):
        await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")


@pytest.mark.asyncio
async def test_admin_retains_post_commit_teardown_failure(monkeypatch) -> None:
    import mcpo.proxy as proxy_module
    from mcpo.api.routers import admin as admin_module

    @asynccontextmanager
    async def live_lifespan(_app):
        yield

    class LiveProxy:
        def http_app(self, **kwargs):
            return FastAPI(lifespan=live_lifespan)

    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: LiveProxy(),
    )
    monkeypatch.setattr(
        admin_module,
        "get_state_manager",
        lambda: type(
            "EnabledState",
            (),
            {"is_server_enabled": lambda self, _name: True},
        )(),
    )
    monkeypatch.setattr(admin_module, "_FASTMCP_AVAILABLE", True)

    class BrokenOldLifespan:
        async def __aexit__(self, exc_type, exc, traceback):
            raise RuntimeError("old teardown failed")

    main_app = FastAPI()
    old_app = FastAPI()
    old_app.state.is_fastmcp_proxy = True
    main_app.mount("/mcp", old_app)
    old_lifespan = BrokenOldLifespan()
    main_app.state.fastmcp_proxy_lifespans = {"/mcp": old_lifespan}
    main_app.state.config_data = {
        "mcpServers": {"next": {"command": "echo", "args": ["next"]}}
    }

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

    failures = main_app.state.fastmcp_proxy_teardown_failures
    assert len(failures) == 1
    failure = next(iter(failures.values()))
    assert failure["lifespan"] is old_lifespan
    assert failure["error"] == "RuntimeError: old teardown failed"
    assert {
        route.path
        for route in main_app.router.routes
        if bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
    } == {"/mcp", "/mcp/next"}

    await admin_module._shutdown_fastmcp_proxy(main_app)


@pytest.mark.asyncio
async def test_admin_shutdown_serializes_with_remount_and_closes_gate(
    monkeypatch,
) -> None:
    from mcpo.api.routers import admin as admin_module

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_remount(main_app, *, base_path):
        entered.set()
        await release.wait()

    monkeypatch.setattr(
        admin_module,
        "_mount_or_remount_fastmcp_unlocked",
        slow_remount,
    )
    main_app = FastAPI()

    remount_task = asyncio.create_task(
        admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")
    )
    await entered.wait()
    shutdown_task = asyncio.create_task(
        admin_module._shutdown_fastmcp_proxy(main_app)
    )
    await asyncio.sleep(0)
    assert shutdown_task.done() is False

    release.set()
    await remount_task
    await shutdown_task

    assert main_app.state.fastmcp_remount_closed is True
    with pytest.raises(RuntimeError, match="closed during shutdown"):
        await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")

@pytest.mark.asyncio
async def test_plain_proxy_invalid_reload_preserves_live_mounts(
    tmp_path,
    monkeypatch,
):
    import mcpo.proxy as proxy_module

    app = FastAPI()
    old = FastAPI()
    old.state.is_fastmcp_proxy = True
    app.mount("/mcp", old)
    old_mounts = [
        {"id": "mcp", "label": "old", "path": "/mcp", "scope": "global"}
    ]
    app.state.proxy_mounts = old_mounts
    app.state.fastmcp_proxy_lifespans = {}
    config_path = tmp_path / "mcpo.json"
    config_path.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(
        proxy_module,
        "_kill_own_child_process_tree",
        AsyncMock(return_value=0),
    )

    with pytest.raises(json.JSONDecodeError):
        await proxy_module._rebuild_plain_proxy_mounts(
            app,
            config_path=config_path,
            path="/mcp",
            stateless_http=None,
        )

    native_paths = [
        route.path
        for route in app.router.routes
        if getattr(getattr(route, "app", None), "state", None)
        and getattr(route.app.state, "is_fastmcp_proxy", False)
    ]
    assert native_paths == ["/mcp"]
    assert app.state.proxy_mounts is old_mounts


@pytest.mark.asyncio
async def test_plain_proxy_candidate_lifespan_failure_preserves_old_generation(
    tmp_path,
    monkeypatch,
):
    import mcpo.proxy as proxy_module

    class OldLifespan:
        def __init__(self):
            self.exited = False

        async def __aexit__(self, exc_type, exc, traceback):
            self.exited = True

    @asynccontextmanager
    async def broken_lifespan(_app):
        raise RuntimeError("candidate lifespan failed")
        yield

    class BrokenProxy:
        def http_app(self, **kwargs):
            return FastAPI(lifespan=broken_lifespan)

    app = FastAPI()
    old = FastAPI()
    old.state.is_fastmcp_proxy = True
    app.mount("/mcp", old)
    old_lifespan = OldLifespan()
    old_mounts = [
        {"id": "mcp", "label": "old", "path": "/mcp", "scope": "global"}
    ]
    app.state.proxy_mounts = old_mounts
    app.state.fastmcp_proxy_lifespans = {"/mcp": old_lifespan}
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "next": {"command": "echo", "args": ["next"]}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: BrokenProxy(),
    )
    monkeypatch.setattr(
        proxy_module,
        "_enumerate_own_child_process_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        proxy_module,
        "_kill_own_child_process_tree",
        AsyncMock(return_value=0),
    )

    with pytest.raises(RuntimeError, match="candidate lifespan failed"):
        await proxy_module._rebuild_plain_proxy_mounts(
            app,
            config_path=config_path,
            path="/mcp",
            stateless_http=None,
        )

    native_paths = [
        route.path
        for route in app.router.routes
        if getattr(getattr(route, "app", None), "state", None)
        and getattr(route.app.state, "is_fastmcp_proxy", False)
    ]
    assert native_paths == ["/mcp"]
    assert app.state.proxy_mounts is old_mounts
    assert app.state.fastmcp_proxy_lifespans == {"/mcp": old_lifespan}
    assert old_lifespan.exited is False


@pytest.mark.asyncio
async def test_plain_proxy_child_enumeration_failure_cleans_all_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module

    entered = 0
    exited = 0

    @asynccontextmanager
    async def live_lifespan(_app):
        nonlocal entered, exited
        entered += 1
        try:
            yield
        finally:
            exited += 1

    class LiveProxy:
        def http_app(self, **kwargs):
            return FastAPI(lifespan=live_lifespan)

    class OldLifespan:
        def __init__(self):
            self.exited = False

        async def __aexit__(self, exc_type, exc, traceback):
            self.exited = True

    app = FastAPI()
    old = FastAPI()
    old.state.is_fastmcp_proxy = True
    app.mount("/mcp", old)
    old_routes = list(app.router.routes)
    old_lifespan = OldLifespan()
    old_mounts = [
        {"id": "mcp", "label": "old", "path": "/mcp", "scope": "global"}
    ]
    old_lifespans = {"/mcp": old_lifespan}
    app.state.proxy_mounts = old_mounts
    app.state.fastmcp_proxy_lifespans = old_lifespans
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "next": {"command": "echo", "args": ["next"]}
                }
            }
        ),
        encoding="utf-8",
    )
    enumerate_children = AsyncMock(
        side_effect=[
            {101},
            RuntimeError("candidate enumeration failed"),
        ]
    )
    kill_children = AsyncMock(return_value=2)
    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: LiveProxy(),
    )
    monkeypatch.setattr(
        proxy_module,
        "_enumerate_own_child_process_ids",
        enumerate_children,
    )
    monkeypatch.setattr(
        proxy_module,
        "_kill_own_child_process_tree",
        kill_children,
    )

    with pytest.raises(RuntimeError, match="candidate enumeration failed"):
        await proxy_module._rebuild_plain_proxy_mounts(
            app,
            config_path=config_path,
            path="/global",
            stateless_http=True,
        )

    assert entered == 2
    assert exited == 2
    assert app.router.routes == old_routes
    assert app.state.proxy_mounts is old_mounts
    assert app.state.fastmcp_proxy_lifespans is old_lifespans
    assert old_lifespan.exited is False
    kill_children.assert_awaited_once_with(preserve_pids={101})


@pytest.mark.asyncio
async def test_plain_proxy_successful_reload_swaps_then_retires_old_generation(
    tmp_path,
    monkeypatch,
):
    import mcpo.proxy as proxy_module

    class OldLifespan:
        def __init__(self):
            self.exited = False

        async def __aexit__(self, exc_type, exc, traceback):
            self.exited = True

    @asynccontextmanager
    async def live_lifespan(_app):
        yield

    class LiveProxy:
        def http_app(self, **kwargs):
            return FastAPI(lifespan=live_lifespan)

    app = FastAPI()
    old = FastAPI()
    old.state.is_fastmcp_proxy = True
    app.mount("/mcp", old)
    old_lifespan = OldLifespan()
    app.state.proxy_mounts = [
        {"id": "mcp", "label": "old", "path": "/mcp", "scope": "global"}
    ]
    app.state.fastmcp_proxy_lifespans = {"/mcp": old_lifespan}
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "next": {"command": "echo", "args": ["next"]}
                }
            }
        ),
        encoding="utf-8",
    )
    enumerate_children = AsyncMock(side_effect=[{101}, {101, 202}])
    kill_children = AsyncMock(return_value=1)
    monkeypatch.setattr(
        proxy_module,
        "_proxy_no_roots_forward",
        lambda *args, **kwargs: LiveProxy(),
    )
    monkeypatch.setattr(
        proxy_module,
        "_enumerate_own_child_process_ids",
        enumerate_children,
    )
    monkeypatch.setattr(
        proxy_module,
        "_kill_own_child_process_tree",
        kill_children,
    )

    await proxy_module._rebuild_plain_proxy_mounts(
        app,
        config_path=config_path,
        path="/global",
        stateless_http=True,
    )

    native_paths = [
        route.path
        for route in app.router.routes
        if getattr(getattr(route, "app", None), "state", None)
        and getattr(route.app.state, "is_fastmcp_proxy", False)
    ]
    assert native_paths == ["/global", "/mcp", "/next"]
    assert old_lifespan.exited is True
    assert {entry["path"] for entry in app.state.proxy_mounts} == {
        "/global",
        "/mcp",
        "/next",
    }
    kill_children.assert_awaited_once_with(preserve_pids={202})

    await proxy_module._teardown_plain_proxy_mounts(app)

@pytest.mark.asyncio
async def test_plain_proxy_teardown_failure_is_reported_and_retained(
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module

    class BrokenLifespan:
        async def __aexit__(self, exc_type, exc, traceback):
            raise RuntimeError("teardown failed")

    app = FastAPI()
    mounted = FastAPI()
    mounted.state.is_fastmcp_proxy = True
    app.mount("/mcp", mounted)
    broken = BrokenLifespan()
    app.state.fastmcp_proxy_lifespans = {"/mcp": broken}
    app.state.proxy_mounts = [{"path": "/mcp"}]
    kill = AsyncMock(return_value=0)
    monkeypatch.setattr(proxy_module, "_kill_own_child_process_tree", kill)

    with pytest.raises(
        RuntimeError,
        match=r"Failed to close FastMCP lifespan\(s\): /mcp",
    ):
        await proxy_module._teardown_plain_proxy_mounts(app)

    assert app.state.fastmcp_proxy_lifespans == {}
    assert app.state.proxy_mounts == []
    assert not any(
        bool(
            getattr(
                getattr(getattr(route, "app", None), "state", None),
                "is_fastmcp_proxy",
                False,
            )
        )
        for route in app.router.routes
    )
    failure = next(
        iter(app.state.fastmcp_proxy_teardown_failures.values())
    )
    assert failure["lifespan"] is broken
    assert failure["error"] == "RuntimeError: teardown failed"
    kill.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_plain_mount_manager_reports_initial_build_failure(
    tmp_path,
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module

    app = FastAPI()
    startup_future = asyncio.get_running_loop().create_future()
    rebuild = AsyncMock(side_effect=RuntimeError("initial build failed"))
    teardown = AsyncMock(return_value=(0, 0, 0))
    monkeypatch.setattr(proxy_module, "_rebuild_plain_proxy_mounts", rebuild)
    monkeypatch.setattr(proxy_module, "_teardown_plain_proxy_mounts", teardown)

    await proxy_module._plain_proxy_mount_manager(
        app,
        config_path=tmp_path / "mcpo.json",
        path="/mcp",
        stateless_http=None,
        rebuild_queue=asyncio.Queue(),
        startup_future=startup_future,
    )

    with pytest.raises(RuntimeError, match="initial build failed"):
        await startup_future
    teardown.assert_awaited_once_with(app)


@pytest.mark.asyncio
async def test_run_proxy_stops_before_watchers_on_initial_mount_failure(
    tmp_path,
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module

    async def fail_manager(
        api_app,
        *,
        config_path,
        path,
        stateless_http,
        rebuild_queue,
        startup_future,
    ):
        startup_future.set_exception(RuntimeError("initial build failed"))

    def unexpected_watchers(*args, **kwargs):
        pytest.fail("watchers started after initial mount failure")

    monkeypatch.setattr(proxy_module, "_preflight_check_port", lambda *args: None)
    monkeypatch.setattr(
        proxy_module,
        "_load_filtered_config",
        lambda path: (object(), {}),
    )
    monkeypatch.setattr(
        proxy_module,
        "_plain_proxy_mount_manager",
        fail_manager,
    )
    monkeypatch.setattr(
        proxy_module,
        "_start_plain_hot_reload_watchers",
        unexpected_watchers,
    )

    with pytest.raises(RuntimeError, match="initial build failed"):
        await proxy_module.run_proxy(
            config_path=tmp_path / "mcpo.json",
            hot_reload=True,
        )


@pytest.mark.asyncio
async def test_child_process_kill_fails_when_target_pid_remains(
    monkeypatch,
) -> None:
    import mcpo.proxy as proxy_module

    enumerate_children = AsyncMock(
        side_effect=[
            {101},
            {101},
            {101},
            {101},
            {101},
            {101},
        ]
    )

    async def skip_thread(_function, *args, **kwargs):
        return None

    monkeypatch.setattr(
        proxy_module,
        "_enumerate_own_child_process_ids",
        enumerate_children,
    )
    monkeypatch.setattr(proxy_module.asyncio, "to_thread", skip_thread)
    monkeypatch.setattr(proxy_module.asyncio, "sleep", AsyncMock())

    with pytest.raises(
        RuntimeError,
        match=r"Failed to terminate owned MCP child PID\(s\): 101",
    ):
        await proxy_module._kill_own_child_process_tree()

    assert enumerate_children.await_count == 6