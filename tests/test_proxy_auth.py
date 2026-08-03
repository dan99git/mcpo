import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from mcpo import proxy
from mcpo.proxy import (
    _TokenGate,
    _add_mcp_host_origin_guard,
    _mcp_transport_security_kwargs,
    _load_filtered_config,
    _run_oauth_proxy,
    _start_plain_hot_reload_watchers,
    resolve_proxy_api_key,
)
from mcpo.utils.auth import APIKeyMiddleware


def test_resolve_proxy_api_key_prefers_cli_value(monkeypatch):
    monkeypatch.setenv("MCPO_API_KEY", "env-secret")

    assert resolve_proxy_api_key("cli-secret") == "cli-secret"


def test_resolve_proxy_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("MCPO_API_KEY", "env-secret")

    assert resolve_proxy_api_key(None) == "env-secret"


def test_resolve_proxy_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("MCPO_API_KEY", raising=False)

    assert resolve_proxy_api_key(None) is None


def test_main_rejects_invalid_vendor_site_id_at_startup(monkeypatch):
    monkeypatch.setenv("BOS_VENDOR_SITE_ID", "9999")
    monkeypatch.setattr(
        proxy,
        "parse_args",
        lambda: SimpleNamespace(env=None, env_path=None),
    )

    with pytest.raises(ValueError, match="BOS_VENDOR_SITE_ID"):
        proxy.main()


def test_plain_transport_security_allows_documented_docker_host():
    settings = _mcp_transport_security_kwargs()

    assert settings == {
        "host_origin_protection": True,
        "allowed_hosts": ["host.docker.internal"],
    }


def test_oauth_transport_security_uses_public_host_and_origin():
    settings = _mcp_transport_security_kwargs("https://Vendor.Example:8443/base")

    assert settings == {
        "host_origin_protection": True,
        "allowed_hosts": ["vendor.example"],
        "allowed_origins": ["https://vendor.example:8443"],
    }


def test_public_transport_security_rejects_credentialed_url():
    with pytest.raises(ValueError, match="absolute HTTP\\(S\\) URL"):
        _mcp_transport_security_kwargs("https://user:secret@example.test")


def test_plain_origin_guard_runs_before_api_key_authentication():
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key="secret")
    _add_mcp_host_origin_guard(app)

    @app.post("/")
    async def endpoint():
        return {"ok": True}

    with TestClient(app) as client:
        hostile = client.post("/", headers={"Origin": "https://attacker.invalid"})
        missing_auth = client.post("/")

    assert hostile.status_code == 403
    assert hostile.text == "Forbidden Origin"
    assert missing_auth.status_code == 401


def test_oauth_origin_guard_runs_before_per_server_token_gate():
    class RejectingProvider:
        async def verify_token(self, _token):
            return False

    inner = FastAPI()
    outer = FastAPI()
    outer.mount("/", _TokenGate(inner, RejectingProvider()))
    _add_mcp_host_origin_guard(outer, "https://vendor.example")

    with TestClient(outer, base_url="https://vendor.example") as client:
        hostile = client.post("/", headers={"Origin": "https://attacker.invalid"})
        missing_auth = client.post("/")

    assert hostile.status_code == 403
    assert hostile.text == "Forbidden Origin"
    assert missing_auth.status_code == 401


def test_filtered_config_keeps_live_state_servers_mounted(tmp_path, monkeypatch):
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "live-toggle": {"command": "echo"},
                    "static-off": {"command": "echo", "enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )

    def fail_if_state_is_loaded():
        raise AssertionError("structural config loading must not read toggle state")

    monkeypatch.setattr(proxy, "get_state_manager", fail_if_state_is_loaded)
    _cfg, servers = _load_filtered_config(config_path)

    assert set(servers) == {"live-toggle"}


def test_filtered_config_interpolates_backend_url(tmp_path, monkeypatch):
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "building-os": {
                        "type": "streamable-http",
                        "url": "https://${BOS_VENDOR_SITE_ID}.${VENDOR_PUBLIC_DOMAIN}/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BOS_VENDOR_SITE_ID", "44354")
    monkeypatch.setenv("VENDOR_PUBLIC_DOMAIN", "ai.lighting")

    _cfg, servers = _load_filtered_config(config_path)

    assert servers["building-os"]["url"] == "https://44354.ai.lighting/mcp"


def test_plain_hot_reload_watches_config_not_toggle_state(tmp_path, monkeypatch):
    from mcpo.utils import config_watcher

    config_path = tmp_path / "mcpo.json"
    config_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    watched_paths = []

    class FakeWatcher:
        def __init__(self, path, _callback):
            watched_paths.append(path)

        def start(self):
            return None

    monkeypatch.setattr(config_watcher, "ConfigWatcher", FakeWatcher)

    watchers = _start_plain_hot_reload_watchers(
        asyncio.Queue(),
        config_path=config_path,
    )

    assert len(watchers) == 1
    assert watched_paths == [str(config_path)]


def test_oauth_hot_reload_watches_config_not_toggle_state(tmp_path, monkeypatch):
    captured = {}

    class FakeConfig:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    class FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            return None

    monkeypatch.setattr(proxy.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(proxy.uvicorn, "Server", FakeServer)
    config_path = tmp_path / "mcpo.json"

    asyncio.run(
        _run_oauth_proxy(
            cfg=None,
            servers={},
            config_path=config_path,
            host="127.0.0.1",
            port=8351,
            public_url="https://example.test",
            api_key="secret",
            storage_path=tmp_path / ".mcpo_oauth_state.json",
            stateless_http=None,
            log_level="info",
            hot_reload=True,
        )
    )

    assert captured["kwargs"]["reload_includes"] == ["*mcpo.json"]
