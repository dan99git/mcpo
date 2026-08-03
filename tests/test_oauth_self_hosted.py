import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from starlette.applications import Starlette
from starlette.requests import Request

from mcpo.utils.oauth_self_hosted import (
    KeyGatedOAuthProvider,
    _loopback_form_action_source,
    _security_headers,
)


def _form_action_directive(csp):
    return next(
        directive.strip()
        for directive in csp.split(";")
        if directive.strip().startswith("form-action ")
    )


def test_loopback_callback_adds_only_exact_ipv4_origin_to_csp():
    redirect_url = "http://127.0.0.1:63335/callback/state?code=secret"

    csp = _security_headers(redirect_url)["Content-Security-Policy"]

    assert (
        _form_action_directive(csp)
        == "form-action 'self' https: http://127.0.0.1:63335"
    )
    assert "callback" not in csp
    assert "code=secret" not in csp


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://127.0.0.2:49152/callback",
        "http://localhost:49152/callback",
        "http://192.168.1.10:49152/callback",
        "http://127.0.0.1.evil.example:49152/callback",
        "https://127.0.0.1:49152/callback",
        "http://127.0.0.1:bad/callback",
        "http://127.0.0.1/callback",
        "http://[::1]:49152/callback",
    ],
)
def test_non_codex_loopback_callback_is_not_added(redirect_url):
    assert _loopback_form_action_source(redirect_url) is None


def test_non_loopback_http_callback_is_not_added_to_csp():
    csp = _security_headers("http://attacker.example:63335/callback")[
        "Content-Security-Policy"
    ]

    assert "attacker.example" not in csp
    assert _form_action_directive(csp) == "form-action 'self' https:"


def _provider_with_pending_transaction(redirect_url):
    provider = object.__new__(KeyGatedOAuthProvider)
    provider._api_key = "correct-key"
    provider._pending = {
        "transaction": {
            "client": None,
            "params": SimpleNamespace(redirect_uri=redirect_url),
            "csrf": "csrf",
            "expires_at": time.time() + 60,
        }
    }
    return provider


def _mounted_provider_with_pending_transaction(tmp_path, redirect_url):
    provider = KeyGatedOAuthProvider(
        base_url="https://dev.ai.lighting",
        api_key="correct-key",
        storage_path=tmp_path / "oauth-state.json",
    )
    provider._pending = _provider_with_pending_transaction(redirect_url)._pending
    return provider


async def _asgi_request(app, method, url, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://dev.ai.lighting",
    ) as client:
        return await client.request(method, url, **kwargs)


def _consent_get_request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/consent",
            "raw_path": b"/consent",
            "query_string": b"txn_id=transaction",
            "headers": [],
            "scheme": "https",
            "server": ("dev.ai.lighting", 443),
            "client": ("127.0.0.1", 49152),
            "root_path": "",
            "http_version": "1.1",
        }
    )


def test_consent_get_uses_registered_loopback_origin():
    provider = _provider_with_pending_transaction(
        "http://127.0.0.1:63335/callback/state"
    )

    response = provider._consent_get(_consent_get_request())

    assert response.status_code == 200
    csp = response.headers["Content-Security-Policy"]
    assert (
        _form_action_directive(csp)
        == "form-action 'self' https: http://127.0.0.1:63335"
    )
    assert "callback" not in csp


def test_consent_get_keeps_https_callback_policy():
    provider = _provider_with_pending_transaction(
        "https://chatgpt.com/connector_platform_oauth_redirect"
    )

    response = provider._consent_get(_consent_get_request())

    assert (
        _form_action_directive(response.headers["Content-Security-Policy"])
        == "form-action 'self' https:"
    )


def test_mounted_consent_get_dispatches_with_loopback_csp(tmp_path):
    provider = _mounted_provider_with_pending_transaction(
        tmp_path,
        "http://127.0.0.1:63335/callback/state",
    )
    app = Starlette(routes=provider.get_routes())

    response = asyncio.run(
        _asgi_request(app, "GET", "/consent?txn_id=transaction")
    )

    assert response.status_code == 200
    assert (
        _form_action_directive(response.headers["Content-Security-Policy"])
        == "form-action 'self' https: http://127.0.0.1:63335"
    )


class _FormRequest:
    async def form(self):
        return {
            "txn_id": "transaction",
            "api_key_csrf": "csrf",
            "api_key": "wrong-key",
        }


def test_wrong_key_rerender_keeps_registered_loopback_origin():
    provider = _provider_with_pending_transaction(
        "http://127.0.0.1:63335/callback/state"
    )

    response = asyncio.run(provider._consent_post(_FormRequest()))

    assert response.status_code == 401
    assert (
        _form_action_directive(response.headers["Content-Security-Policy"])
        == "form-action 'self' https: http://127.0.0.1:63335"
    )


def test_mounted_correct_key_post_redirects_and_consumes_transaction(
    tmp_path, monkeypatch
):
    callback_url = (
        "http://127.0.0.1:63335/callback/state?code=authorization-code"
        "&state=client-state"
    )

    async def _authorize(_provider, _client, _params):
        return callback_url

    monkeypatch.setattr(InMemoryOAuthProvider, "authorize", _authorize)
    provider = _mounted_provider_with_pending_transaction(
        tmp_path,
        "http://127.0.0.1:63335/callback/state",
    )
    app = Starlette(routes=provider.get_routes())

    response = asyncio.run(
        _asgi_request(
            app,
            "POST",
            "/consent",
            data={
                "txn_id": "transaction",
                "api_key_csrf": "csrf",
                "api_key": "correct-key",
            },
            follow_redirects=False,
        )
    )

    assert response.status_code == 302
    assert response.headers["Location"] == callback_url
    assert "transaction" not in provider._pending
    assert (
        _form_action_directive(response.headers["Content-Security-Policy"])
        == "form-action 'self' https: http://127.0.0.1:63335"
    )
