"""Self-hosted OAuth 2.1 Authorization Server for the mcpo FastMCP proxy.

Lets an MCP client that speaks the OAuth flow (e.g. ChatGPT custom connectors)
connect to the proxy over a public tunnel. The authorization server is
self-contained: there is NO external identity provider. The single human gate is
the existing mcpo API key, entered on a consent page during the /authorize step.
A correct key mints the authorization code; everything after that is the standard
OAuth 2.1 / PKCE machinery inherited from fastmcp's InMemoryOAuthProvider.

Discovery: because this is a full OAuthProvider, fastmcp auto-mounts the RFC 8414
authorization-server metadata and RFC 9728 protected-resource metadata routes, plus
/authorize, /token and /register (RFC 7591 dynamic client registration), which is
exactly the discovery chain a client runs against the server URL.

Persistence: registered clients and issued access/refresh tokens are written to a
JSON file so a proxy restart does not force the client to re-authorise. In-flight
login transactions and single-use 5-minute authorization codes are kept in memory
only; losing those on restart is harmless.
"""

import hmac
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from mcp.server.auth.provider import AuthorizationParams
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

logger = logging.getLogger(__name__)

# How long a pending /authorize -> /consent login may sit before it expires.
CONSENT_TXN_TTL_SECONDS = 10 * 60

_BASE_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _loopback_form_action_source(redirect_url: str) -> Optional[str]:
    """Return the exact Codex IPv4 loopback origin allowed for a callback."""
    try:
        parsed = urlsplit(redirect_url)
        if parsed.scheme.lower() != "http" or parsed.hostname != "127.0.0.1":
            return None
        port = parsed.port
        if port is None:
            return None
    except (ValueError, TypeError):
        return None

    return f"http://127.0.0.1:{port}"


def _security_headers(redirect_url: Optional[str] = None) -> dict[str, str]:
    # The browser applies form-action to the post-consent redirect. HTTPS callbacks
    # are allowed generally. Codex uses an ephemeral HTTP 127.0.0.1 callback, so
    # allow only the exact registered IPv4 loopback origin for this transaction.
    form_actions = ["'self'", "https:"]
    if redirect_url:
        loopback_source = _loopback_form_action_source(redirect_url)
        if loopback_source:
            form_actions.append(loopback_source)
    headers = dict(_BASE_SECURITY_HEADERS)
    headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        f"form-action {' '.join(form_actions)}"
    )
    return headers


class KeyGatedOAuthProvider(InMemoryOAuthProvider):
    """OAuth AS whose /authorize step is gated by the mcpo API key.

    Inherits all token issuance, refresh and revocation logic from
    InMemoryOAuthProvider. The only behavioural change is that authorize() defers
    code issuance to a consent page that verifies the API key before minting a code.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        storage_path: Path,
    ):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        if not api_key:
            raise ValueError("KeyGatedOAuthProvider requires a non-empty api_key")
        self._api_key = api_key
        self._storage_path = Path(storage_path)
        # txn_id -> {"client", "params", "csrf", "expires_at"}
        self._pending: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Authorize: hand off to the consent page instead of minting a code. #
    # ------------------------------------------------------------------ #
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self._prune_pending()
        txn_id = secrets.token_urlsafe(32)
        self._pending[txn_id] = {
            "client": client,
            "params": params,
            "csrf": secrets.token_urlsafe(32),
            "expires_at": time.time() + CONSENT_TXN_TTL_SECONDS,
        }
        return f"{str(self.base_url).rstrip('/')}/consent?txn_id={txn_id}"

    def _prune_pending(self) -> None:
        now = time.time()
        for tid in [t for t, v in self._pending.items() if v["expires_at"] < now]:
            self._pending.pop(tid, None)

    # ------------------------------------------------------------------ #
    # Routes: add /consent on top of the inherited OAuth AS routes.      #
    # ------------------------------------------------------------------ #
    def get_routes(self, mcp_path: Optional[str] = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        routes.append(
            Route("/consent", endpoint=self._handle_consent, methods=["GET", "POST"])
        )
        return routes

    async def _handle_consent(self, request: Request) -> Response:
        if request.method == "GET":
            return self._consent_get(request)
        return await self._consent_post(request)

    def _consent_get(self, request: Request) -> Response:
        txn_id = request.query_params.get("txn_id", "")
        txn = self._valid_txn(txn_id)
        if txn is None:
            return self._error_page("This login link has expired. Start again from your client.")
        return self._form_page(
            txn_id,
            txn["csrf"],
            redirect_url=str(txn["params"].redirect_uri),
        )

    async def _consent_post(self, request: Request) -> Response:
        form = await request.form()
        txn_id = str(form.get("txn_id", ""))
        csrf = str(form.get("api_key_csrf", ""))
        submitted_key = str(form.get("api_key", ""))

        txn = self._valid_txn(txn_id)
        if txn is None:
            return self._error_page("This login link has expired. Start again from your client.")

        if not hmac.compare_digest(csrf, txn["csrf"]):
            # CSRF mismatch: refuse and burn the transaction.
            self._pending.pop(txn_id, None)
            return self._error_page("Security check failed. Start again from your client.")

        if not hmac.compare_digest(submitted_key, self._api_key):
            # Wrong key: keep the transaction alive so the user can retry within the TTL.
            return self._form_page(
                txn_id,
                txn["csrf"],
                error="Incorrect key.",
                status_code=401,
                redirect_url=str(txn["params"].redirect_uri),
            )

        # Correct key. Consume the transaction and mint the authorization code via the
        # inherited implementation, which returns the client's redirect_uri with code+state.
        self._pending.pop(txn_id, None)
        redirect_url = await InMemoryOAuthProvider.authorize(
            self, txn["client"], txn["params"]
        )
        self._save()
        resp = RedirectResponse(url=redirect_url, status_code=302)
        for k, v in _security_headers(redirect_url).items():
            resp.headers[k] = v
        return resp

    def _valid_txn(self, txn_id: str) -> Optional[dict]:
        self._prune_pending()
        if not txn_id:
            return None
        return self._pending.get(txn_id)

    # ------------------------------------------------------------------ #
    # HTML                                                                #
    # ------------------------------------------------------------------ #
    def _html(
        self,
        body: str,
        status_code: int = 200,
        redirect_url: Optional[str] = None,
    ) -> HTMLResponse:
        page = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>mcpo authorization</title><style>"
            "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0f1115;"
            "color:#e6e6e6;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}"
            ".card{background:#181b22;border:1px solid #2a2f3a;border-radius:12px;padding:28px;"
            "width:min(92vw,360px);box-shadow:0 8px 30px rgba(0,0,0,.4)}"
            "h1{font-size:18px;margin:0 0 4px}p{color:#9aa4b2;font-size:13px;margin:0 0 18px}"
            "label{display:block;font-size:13px;margin:0 0 6px}"
            "input[type=password]{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;"
            "border:1px solid #2a2f3a;background:#0f1115;color:#e6e6e6;font-size:14px}"
            "button{margin-top:16px;width:100%;padding:10px 12px;border:0;border-radius:8px;"
            "background:#3b82f6;color:#fff;font-size:14px;font-weight:600;cursor:pointer}"
            ".err{background:#3a1d1d;border:1px solid #5b2b2b;color:#ffb4b4;font-size:13px;"
            "padding:8px 10px;border-radius:8px;margin:0 0 14px}"
            "</style></head><body><div class='card'>" + body + "</div></body></html>"
        )
        resp = HTMLResponse(content=page, status_code=status_code)
        for k, v in _security_headers(redirect_url).items():
            resp.headers[k] = v
        return resp

    def _form_page(
        self,
        txn_id: str,
        csrf: str,
        error: str = "",
        status_code: int = 200,
        redirect_url: Optional[str] = None,
    ) -> HTMLResponse:
        err_html = f"<div class='err'>{error}</div>" if error else ""
        body = (
            "<h1>Authorize access</h1>"
            "<p>Enter your mcpo API key to connect this client.</p>"
            f"{err_html}"
            "<form method='post' action='/consent' autocomplete='off' "
            "onsubmit=\"if(window.__authsent){return false}window.__authsent=1;"
            "var b=document.getElementById('authbtn');if(b){b.textContent='Authorizing…';b.style.opacity='0.6'}\">"
            f"<input type='hidden' name='txn_id' value='{txn_id}'>"
            f"<input type='hidden' name='api_key_csrf' value='{csrf}'>"
            "<label for='api_key'>API key</label>"
            "<input id='api_key' name='api_key' type='password' autofocus required>"
            "<button id='authbtn' type='submit'>Authorize</button>"
            "</form>"
        )
        return self._html(
            body,
            status_code=status_code,
            redirect_url=redirect_url,
        )

    def _error_page(self, message: str) -> HTMLResponse:
        return self._html(f"<h1>Cannot continue</h1><p>{message}</p>", status_code=400)

    # ------------------------------------------------------------------ #
    # Persistence: clients + tokens survive restart.                     #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _force_public(client: OAuthClientInformationFull) -> OAuthClientInformationFull:
        """Make a client a public PKCE client (no secret).

        ChatGPT (and other MCP clients) connect with token_endpoint_auth_method=none
        and send no client_secret at /token. The MCP SDK's DCR handler otherwise issues
        a secret to any client that doesn't explicitly register as "none", which then
        makes the token endpoint demand that secret and reject the public exchange with
        "Client secret is required". Stripping the secret keeps every client public.
        """
        try:
            client.token_endpoint_auth_method = "none"
            client.client_secret = None
            client.client_secret_expires_at = None
        except Exception:
            logger.warning("Could not coerce client %s to public", getattr(client, "client_id", "?"), exc_info=True)
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._force_public(client_info)
        await super().register_client(client_info)
        self._save()

    async def exchange_authorization_code(self, client, authorization_code):
        token = await super().exchange_authorization_code(client, authorization_code)
        self._save()
        return token

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save()
        return token

    async def revoke_token(self, token) -> None:
        await super().revoke_token(token)
        self._save()

    def _save(self) -> None:
        try:
            data = {
                "clients": {
                    cid: c.model_dump(mode="json") for cid, c in self.clients.items()
                },
                "access_tokens": {
                    t: a.model_dump(mode="json") for t, a in self.access_tokens.items()
                },
                "refresh_tokens": {
                    t: r.model_dump(mode="json") for t, r in self.refresh_tokens.items()
                },
                "access_to_refresh": dict(self._access_to_refresh_map),
                "refresh_to_access": dict(self._refresh_to_access_map),
            }
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._storage_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, self._storage_path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except Exception:
            logger.warning("Failed to persist OAuth state to %s", self._storage_path, exc_info=True)

    def _load(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            from mcp.server.auth.provider import AccessToken, RefreshToken

            with self._storage_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.clients = {
                cid: self._force_public(OAuthClientInformationFull.model_validate(c))
                for cid, c in data.get("clients", {}).items()
            }
            self.access_tokens = {
                t: AccessToken.model_validate(a)
                for t, a in data.get("access_tokens", {}).items()
            }
            self.refresh_tokens = {
                t: RefreshToken.model_validate(r)
                for t, r in data.get("refresh_tokens", {}).items()
            }
            self._access_to_refresh_map = dict(data.get("access_to_refresh", {}))
            self._refresh_to_access_map = dict(data.get("refresh_to_access", {}))
            logger.info(
                "Loaded OAuth state: %d clients, %d access, %d refresh tokens",
                len(self.clients), len(self.access_tokens), len(self.refresh_tokens),
            )
        except Exception:
            logger.warning("Failed to load OAuth state from %s; starting empty", self._storage_path, exc_info=True)
