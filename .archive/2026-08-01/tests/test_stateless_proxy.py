"""Tests for roots suppression, proxy era propagation, and admin remount flags."""

import mcp.types
import pytest
from fastapi import FastAPI
from fastmcp.mcp_config import MCPConfig
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import (
    FastMCPProxy,
    ProxyClient,
    StatefulProxyClient,
)

from mcpo.proxy import _EraAwareProxyClientFactory, _proxy_no_roots_forward


def _test_cfg() -> MCPConfig:
    return MCPConfig.from_dict(
        {"mcpServers": {"echo": {"command": "echo", "args": ["hi"]}}}
    )


@pytest.mark.asyncio
async def test_stateless_factory_suppresses_roots_and_propagates_modern_era(
    monkeypatch,
) -> None:
    monkeypatch.setattr("mcpo.proxy._proxy_backend_mode", lambda: "2026-07-28")
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    assert isinstance(proxy, FastMCPProxy)
    assert isinstance(proxy.client_factory, _EraAwareProxyClientFactory)
    client = proxy.client_factory()
    assert type(client) is ProxyClient
    assert client.mode == "2026-07-28"
    assert client._transport_options.backend_mode == "2026-07-28"

    callback = client._session_kwargs["list_roots_callback"]
    result = await callback(None)
    assert isinstance(result, mcp.types.ListRootsResult)
    assert result.roots == []


@pytest.mark.asyncio
async def test_stateless_factory_returns_fresh_clients_for_each_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr("mcpo.proxy._proxy_backend_mode", lambda: "legacy")
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    first = proxy.client_factory()
    second = proxy.client_factory()

    assert first is not second
    assert first.mode == second.mode == "legacy"
    assert first._transport_options.backend_mode == "legacy"
    for client in (first, second):
        result = await client._session_kwargs["list_roots_callback"](None)
        assert isinstance(result, mcp.types.ListRootsResult)
        assert result.roots == []


def test_factory_without_request_context_defaults_to_legacy() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg(), stateless=True)

    client = proxy.client_factory()

    assert type(client) is ProxyClient
    assert client.mode == "legacy"


@pytest.mark.asyncio
async def test_stateful_factory_keeps_separate_era_bases_and_suppresses_roots() -> None:
    proxy = _proxy_no_roots_forward(_test_cfg())
    factory = proxy.client_factory

    assert isinstance(factory, _EraAwareProxyClientFactory)
    modern = factory.client_for_mode("2026-07-28")
    legacy = factory.client_for_mode("legacy")
    assert isinstance(modern, StatefulProxyClient)
    assert isinstance(legacy, StatefulProxyClient)
    assert modern is not legacy
    assert modern.mode == "2026-07-28"
    assert modern._transport_options.backend_mode == "2026-07-28"
    assert legacy.mode == "legacy"
    result = await modern._session_kwargs["list_roots_callback"](None)
    assert isinstance(result, mcp.types.ListRootsResult)
    assert result.roots == []


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


async def _run_admin_remount(monkeypatch, *, proxy_stateless):
    import fastmcp.server
    from mcpo.api.routers import admin as admin_module

    recorded: list = []
    monkeypatch.setattr(
        fastmcp.server,
        "create_proxy",
        lambda cfg: _FakeRemoteProxy(recorded),
    )

    main_app = FastAPI()
    main_app.state.config_data = {
        "mcpServers": {"srv1": {"command": "echo", "args": ["hi"]}}
    }
    if proxy_stateless is not None:
        main_app.state.proxy_stateless = proxy_stateless

    await admin_module._mount_or_remount_fastmcp(main_app, base_path="/mcp")
    return recorded


@pytest.mark.asyncio
async def test_admin_remount_passes_stateless_and_transport_security(monkeypatch) -> None:
    recorded = await _run_admin_remount(monkeypatch, proxy_stateless=True)

    assert [item["stateless"] for item in recorded] == [True, True]
    assert all(item["host_origin_protection"] is True for item in recorded)
    assert all(
        item["middleware"][-2:]
        == ["CodeModeMCPMiddleware", "MCPToolFilterMiddleware"]
        for item in recorded
    )


@pytest.mark.asyncio
async def test_admin_remount_defaults_to_stateful(monkeypatch) -> None:
    recorded = await _run_admin_remount(monkeypatch, proxy_stateless=None)
    assert [item["stateless"] for item in recorded] == [None, None]
