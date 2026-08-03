# mcpo over OAuth tunnel — ChatGPT & Claude Desktop setup

Working notes for exposing mcpo's MCP servers to ChatGPT and Claude Desktop over a
public, OAuth-protected Cloudflare tunnel. Kept as the running record of what was
built, every bug hit and its fix, the infra setup, and the known gaps.

Last updated: 2026-07-23.

> Scope: this file documents the general developer MCPO service at
> `dev.ai.lighting` and its self-hosted OAuth experiment. It is not the
> Vendor Connector or Site Connector Container deployment contract. The
> separate-machine POC is documented in
> `docs/VENDOR_CONNECTOR_CLIENT_GATEWAY_VM_POC.md`. The target site runtime
> requires a separate OAuth authority and fixed private vendor ingress.

---

## What this does

- Serves the mcpo aggregate MCP endpoint (all enabled servers' tools) at a public
  HTTPS URL that remote MCP clients (ChatGPT custom connectors, Claude Desktop remote
  connectors) can reach.
- Puts a self-hosted OAuth 2.1 authorization server in front, because ChatGPT's custom
  connector only offers OAuth / No-auth (no static-key option). The single human gate is
  the existing mcpo API key, entered on a consent page during the OAuth flow.

## Endpoints (public, via tunnel)

- Base: `https://dev.ai.lighting`
- Aggregate MCP: `https://dev.ai.lighting/mcp`  (all enabled servers, 44 tools currently)
- Per-server: `https://dev.ai.lighting/<name>/`  (e.g. `/filesystem/`, note trailing slash)
- OAuth discovery: `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`
- OAuth ops: `/authorize`, `/token`, `/register` (DCR), `/consent` (paste-key page)

## Local layout

- OAuth proxy (this work): `python -m mcpo.proxy --oauth` on **127.0.0.1:8351**, tunnelled to `dev.ai.lighting`.
- Admin UI: `python -m mcpo serve` on **:8000** → `http://localhost:8000/ui` (manages servers/tools).
- Normal proxy (OpenWebUI etc.): `python -m mcpo.proxy` on **:8001** (start.bat).
- cloudflared: Windows service, tunnel `ai.lighting` (id `4979962f-ecfd-4eff-9c64-cdf19d2fa125`).

### MCP proxy parity invariant

Ports `8001` and `8351` expose the same servers, tools, filtering, calls,
code-mode behavior, and toggle state on aggregate `/mcp` and per-server
endpoints. Port `8351` adds only OAuth discovery, authorization, token
enforcement, and public URL handling. The `/global` route on port `8001`
remains a legacy alias.

## Run command (OAuth proxy)

```
$env:PYTHONPATH='D:/vibe-coded-projects/mcpo/src'
D:/vibe-coded-projects/mcpo/.venv/Scripts/python.exe -m mcpo.proxy `
  --config D:/vibe-coded-projects/mcpo/mcpo.json `
  --env-path D:/vibe-coded-projects/mcpo/.env `
  --host 127.0.0.1 --port 8351 `
  --api-key testkey123 --oauth --public-url https://dev.ai.lighting
```
- Consent-page key (what you paste in the browser during connect): the `--api-key` value (`testkey123` in testing).
- `--public-url` MUST be the https tunnel URL, or OAuth discovery advertises unreachable endpoints.

---

## Code changes (all in-repo)

### New: `src/mcpo/utils/oauth_self_hosted.py`
`KeyGatedOAuthProvider` (subclasses fastmcp `InMemoryOAuthProvider`):
- `/consent` HTML page — paste the API key; validated with `hmac.compare_digest`; CSRF token per transaction; double-submit guarded by an inline JS `onsubmit` flag.
- `authorize()` returns a redirect to `/consent` instead of minting a code immediately; the code is minted only after the key is verified.
- `_force_public()` — strips any `client_secret` and sets `token_endpoint_auth_method="none"` on every registered client (applied on register AND on load). Fix for the client-secret bug below.
- Persists clients + access/refresh tokens to `.mcpo_oauth_state.json` (atomic write) so a proxy restart doesn't force re-auth.
- Security headers with HTTPS callback support plus the exact registered
  `http://127.0.0.1:<port>` origin used by Codex CLI.

### Changed: `src/mcpo/proxy.py`
- New CLI flags: `--oauth`, `--public-url`, `--hot-reload`.
- `_build_oauth_app()` — builds the FastAPI app: aggregate `/mcp` (OAuth-gated, serves discovery at root) + per-server `/<name>/` (token-gated via `_TokenGate`).
- `_run_oauth_proxy()`, `oauth_app_factory()`, `_load_filtered_config()` (reloads structural config; live toggle state is enforced per request).
- `_TokenGate` — requires a valid OAuth access token on per-server endpoints (reuses `provider.verify_token`).
- `_proxy_no_roots_forward()` — builds the proxy with `ProxyClient(cfg, roots=[])`. Fix for the roots teardown below.
- Aggregate and per-server MCP routes use the same stream-safe tool filtering and code-mode behavior as port `8001`.

### Changed: dead-path fixes (approved)
- `.vscode/tasks.json`, `mcpo.json`: `D:\mcpo\...` → `D:\vibe-coded-projects\mcpo\...`.

---

## Bugs hit and fixed (the load-bearing ones)

1. **Client secret required (401 on /token).** ChatGPT registers as a public PKCE client
   and sends no secret, but the MCP SDK's DCR handler issues a secret to any client that
   doesn't explicitly register `token_endpoint_auth_method=none`, then the token endpoint
   demands it. → `_force_public()` strips the stored secret. Symptom before fix: connect
   hangs on "Authorizing", no `/token` completes.

2. **CSP blocked OAuth redirects.** Consent originally used `form-action 'self'`, which
   stopped the post-consent 302 to `https://chatgpt.com/...`. Allowing only `https:` fixed
   ChatGPT but still blocked Codex CLI's `http://127.0.0.1:<port>/callback` and left
   `codex mcp login` waiting with no `/token` request. The policy now adds only the exact
   registered `http://127.0.0.1:<port>` origin for that transaction; every other HTTP
   callback stays blocked.

3. **Roots teardown (session dies ~14s, "no callable tools").** FastMCP's proxy forwards a
   backend server's `roots/list` request to the connecting client; ChatGPT/Claude don't
   implement roots, so it errors and an "unknown request ID" stream exception tears the
   session down before tools register. → `ProxyClient(cfg, roots=[])` answers roots locally.

4. **Historical tool-filter ASGI crash (connected but zero tools).** The first OAuth
   implementation reused the old buffer-and-resend response filter. It corrupted
   streamable-HTTP ASGI framing. The replacement applies the same stream-safe MCP
   filtering on ports `8001` and `8351` without touching OAuth control routes.

5. **Double-submit → "This login link has expired".** Clicking Authorize twice: first click
   consumes the one-time transaction, second shows the expired page and confuses the client.
   → inline JS guard disables re-submit.

6. **Tunnel token invalid (all hostnames 502).** cloudflared service held an OLD token after
   a rotate; edge rejected it ("Unauthorized: Invalid tunnel secret"). → reinstalled the
   service with the current token (admin: `cloudflared service uninstall` then `service install <token>`).

7. **"Unknown tool" / intermittent 0 tools.** The default proxy client spawns backend
   subprocesses fresh per request, so a session's tools/list races ahead of the backends
   finishing init — clients get 0/partial tools and calls hit "Unknown tool" (ChatGPT got
   1/8). → use `StatefulProxyClient(cfg, roots=[])` with `client_factory=base.new_stateful`
   (via `FastMCPProxy`), which keeps one warm backend connection per session. Verified:
   320 tools list and calls succeed (get_config, filesystem, powershell all OK).
   Cost: the FIRST connect spins up every enabled backend at once, so with heavy servers
   (xero-code-mode = 337 ops, kicad via uvx, workspace-mcp) it can take ~2 min; retry if a
   client times out, or disable heavy servers on the tunnel for snappy connects.

---

## Cloudflare dashboard setup (done manually)

- **Public hostname**: tunnel `ai.lighting` → add `dev.ai.lighting` → HTTP `localhost:8351`
  (auto-creates the DNS CNAME to `<tunnelid>.cfargotunnel.com`, proxied).
- **Access bypass**: the `ai.lighting` zone had a Cloudflare Access app ("Novon Demo")
  requiring login; it was 403-ing ChatGPT's server-side requests. Added a self-hosted Access
  application for `dev.ai.lighting` with a **Bypass / Everyone** policy so the subdomain is
  exempt (our own OAuth does the real auth). Access matches most-specific host first.
- Bot Fight Mode / Block-AI-bots: confirmed off (were not the blocker).

---

## Client config

**ChatGPT** (custom connector, developer/Apps mode):
- Server URL: `https://dev.ai.lighting/mcp`, Authentication: OAuth.
- Registration method: DCR. Leave OAuth Client ID / Secret blank.
- On connect: consent page → paste the API key.

**Claude Desktop** — two options (use ONE):
- Native "Add custom connector": URL `https://dev.ai.lighting/mcp`, leave OAuth ID/Secret blank.
- Config file `%APPDATA%\Claude\claude_desktop_config.json` via `mcp-remote`:
  ```json
  "mcpServers": { "mcpo-dev": { "command": "npx", "args": ["-y","mcp-remote","https://dev.ai.lighting/mcp"] } }
  ```
  (If using the native connector, remove this entry to avoid a duplicate.)

**Codex CLI**:
- Register `https://dev.ai.lighting/mcp`, then run `codex mcp login mcpo`.
- Codex uses an ephemeral `http://127.0.0.1:<port>/callback/...` listener. The consent
  CSP must include that exact loopback origin or the client waits before `/token`.

---

## Current behavior / follow-ups

- `start.bat` launches the `8351` OAuth proxy alongside the `8001` proxy.
- Structural config changes rebuild proxy routes on both ports.
- Server and tool state changes use the shared live filter and do not restart
  the proxies or their MCP server processes.
- Disabled tools are removed from `tools/list` and rejected by `tools/call`
  on aggregate and per-server endpoints on both ports.
- OAuth discovery, authorization, consent, registration, token validation, and
  public URL handling are the only behavior added by port `8351`.
- **8000 admin ↔ 8001 proxy 401**: launched inconsistently this session (8001 picked up
  `MCPO_API_KEY` from `.env`, 8000 admin launched without it) so the UI log panel 401s.
  Running `start.bat` launches both with matching auth and clears it.
