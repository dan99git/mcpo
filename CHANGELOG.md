# Changelog

All notable changes to this project are recorded here. Newest entry first.
Every change set that touches `src/`, `static/`, `tests/`, or dependencies MUST
add an entry under an `## Unreleased` heading in the same commit — enforced by
`.githooks/pre-commit` (enable with `git config core.hooksPath .githooks`).

## Unreleased (dev) — 2026-08-10 (UI log buffer loses uvicorn lines)

### Fixed
- The in-UI log view silently missed all uvicorn access/error lines: uvicorn's
  dictConfig (inside `uvicorn.Config.__init__`) wipes handler attachments, and
  only the rotating FILE handler was being reattached afterwards.
  `reattach_uvicorn_file_handlers` (`src/mcpo/services/file_logging.py`) now
  reattaches `BufferedLogHandler` too, covering every existing call site in
  serve and proxy. Found by the file-logging worker on 2026-08-03, flagged as
  pre-existing then; fixed now.

### Verified
- New `tests/test_ui_log_buffer_reattach.py` (2 tests): buffer proven severed
  by a simulated dictConfig wipe, restored after reattach, idempotent on
  double-call. Full suite 779 passed / 0 failed / 2 skipped.

## Unreleased (dev) — 2026-08-10 (watchdog startup race + port-8000 auth posture)

### Fixed
- Watchdog startup race (`tools/watchdog.ps1`): the first probe cycle fired
  seconds after `start.bat` launched the services, before their listeners
  bound, spawning duplicate consoles (observed 2026-08-01 21:20:56 and
  21:26:02). New `StartupGraceSeconds` (default 90): a service never yet seen
  alive is not restarted inside the grace window. Services seen alive once are
  restarted immediately as before.

### Changed
- Port 8000 posture (`start.bat` + watchdog mirror, authored in a prior Codex
  session, committed here): `--strict-auth` removed because it has no path
  exemptions and made `/ui` unreachable from a browser; compensated by binding
  8000 to 127.0.0.1 (local-only). Production exposure remains the 8351 OAuth
  tunnel. `--api-key` auth on 8000/8001 unchanged.
- `mcpo_state.json`: removed 4 entries naming servers absent from mcpo.json
  (`mcpo`, `new`, `new_server`, `s1` — test-write leftovers from before the
  state-isolation fixes). All 23 real server entries byte-identical. Original
  archived at `.archive/2026-08-10/mcpo_state.json`. (State file is gitignored;
  recorded here for the audit trail.)

### Verified
- Parse check clean; dry-run with `-StartupGraceSeconds 6 -IntervalSeconds 7
  -MaxCycles 2`: cycle 1 logged "inside startup grace - not restarting" for the
  two dead ports, cycle 2 (grace expired) escalated to DOWN + would-relaunch.

## Unreleased (dev) — 2026-08-03 (persistent rotating file logs)

### Added
- Persistent rotating file logging (`src/mcpo/services/file_logging.py`):
  `RotatingFileHandler` on the root logger, 5 MB per file, 3 backups, UTF-8,
  full tracebacks. Survives restarts, unlike the in-memory UI log buffer.
  Files derive from mode + port so the three start.bat processes never
  collide: serve -> `logs/openapi.log`, proxy 8001 -> `logs/proxy-8001.log`,
  OAuth proxy 8351 -> `logs/proxy-8351.log`. Directory override via
  `MCPO_LOG_DIR` (default `<repo>/logs`, created if missing). If the file
  cannot be opened a loud stderr warning is printed and the process keeps
  running on console/UI logging (no crash, no silent failure).
- Wired in both entrypoints: `build_main_app` (serve) and `run_proxy`
  (proxy + OAuth). uvicorn's dictConfig (inside `uvicorn.Config.__init__`)
  strips handlers from the non-propagating `uvicorn`/`uvicorn.access`
  loggers, so `reattach_uvicorn_file_handlers()` runs after each
  `uvicorn.Config(...)` to keep access/error lines on disk. OAuth
  hot-reload runs the app in a reloader child process; only the child
  attaches the file (in `oauth_app_factory`) because two processes holding
  one rotating file breaks rollover on Windows.

### Changed
- `start.bat` no longer truncates `logs/openapi.log`/`logs/proxy.log` at
  launch; the apps own their log files now and history persists.
- `tests/conftest.py` points `MCPO_LOG_DIR` at a temp dir so pytest runs
  stop appending test noise into the real debug logs.

### Verified
- `tests/test_file_logging.py` (9 tests): filename derivation per mode/port,
  `MCPO_LOG_DIR` override, rotation config (5 MB / 3 / utf-8), line +
  traceback written, idempotent attach, stderr warning on unopenable dir,
  uvicorn reattach without duplicates. Full suite 777 passed / 0 failed /
  2 skipped (baseline 768, +9 new). Live: serve on 18000 and proxy on 18001
  wrote startup, uvicorn access (404/401) lines and real tracebacks to
  `logs/openapi.log` and `logs/proxy-18001.log`.

## Unreleased (trial/codex-keys) — 2026-07-31 (per-key usage accounting)

### Added
- Per-key usage accounting (`src/mcpo/services/usage.py`): requests, errors,
  rate-limited count, per-path counts, last status/path/time — recorded by
  `ModelAPIKeyMiddleware` on every model-key response (`src/mcpo/utils/auth.py`).
  Answers "which client burned my Codex quota". In-memory by design (the audit
  proved file writes in the auth hot path freeze the event loop): counters are
  per worker process and reset on restart, declared via `"usageScope": "process"`
  and `"sinceStartup": true` in responses. Admin-key requests are not counted.
- Surfaced in: key list (`GET /chat/providers/codex-oauth/access-keys` — `usage`
  per key), `GET /v1/whoami` (caller's own usage), and the Settings key rows
  ("Requests: N (M errors) since restart", `static/ui/js/pages/settings.js`).
- Deliberately absent: token counts — the middleware cannot read streaming bodies
  without buffering them; token accounting belongs in the completions provider
  if wanted later.

### Verified
- `tests/test_usage_accounting.py`: recorder counts requests/errors/429/paths;
  middleware counts model-key requests and not admin; usage appears in key list
  and whoami. Full suite 683 passed / 15 failed (same pre-existing) / 4 skipped.

## Unreleased (trial/codex-keys) — 2026-07-30 (adversarial audit fixes)

Fixes from an adversarial security audit of the Codex-OAuth key gateway. Auth
bypass, scope escalation, path normalization, secret handling, SSRF and
multi-process write safety were all probed and came back clean.

### Fixed
- HIGH: blocking file-locked store reads ran synchronously in async middleware, so
  one worker holding the lock froze another worker's whole event loop (proven: an
  unrelated request stalled 3.6s). `ModelAPIKeyMiddleware` now off-loads
  `enforcement_enabled` and `authenticate` via `asyncio.to_thread`
  (`src/mcpo/utils/auth.py`).
- HIGH: a non-ASCII bearer token hit `hmac.compare_digest(str, str)` which raises
  TypeError, returning a 500 that acted as a middleware oracle. New byte-safe
  `_credentials_match` (`src/mcpo/utils/auth.py`) returns False instead of raising;
  used for both Bearer and Basic admin compares.
- MED: CORS was the innermost middleware, so 401/403/429 from auth carried no CORS
  headers and browser clients saw opaque failures. CORS is now added last (outermost)
  in `src/mcpo/main.py`. Live-verified: a 401 now returns `access-control-allow-origin`.
- MED: `rotate_key` reset `revokedAt=None`, silently reactivating a revoked key.
  It now refuses to rotate a revoked key (`ModelAPIKeyStoreError` -> 409 at the
  endpoint) (`src/mcpo/services/model_api_keys.py`, router).
- MED: the open-`/v1` startup warning pointed at an endpoint that returns 503 without
  an admin key. Reworded to point at `MCPO_API_KEY` (`src/mcpo/main.py`).

### Added
- `X-RateLimit-Limit`/`X-RateLimit-Remaining` on successful model-key responses (not
  just 429) so clients can back off early (`src/mcpo/utils/auth.py`).

### Verified
- Tests +3 (non-ASCII compare, rotate-revoked refusal + 409 endpoint, success rate
  headers). Full suite 681 passed / 15 failed (same pre-existing) / 4 skipped.
  Live: CORS header present on a 401 on :18007.

### Audit findings deferred (flagged, not fixed)
- MED: rate limiting lives only in `ModelAPIKeyMiddleware`, not the `get_verify_api_key`
  dependency path (defense-in-depth; both are mounted today).
- MED: no HTTP recovery path for a corrupt key registry (fails closed with 503).
- LOW: auth timing leaks key-id existence (id is in the plaintext prefix anyway);
  `_hits` dict never evicts empty deques (bounded to real key ids today).
- Utility gaps ranked for later: per-key usage accounting, per-key quotas/model
  allowlists, default key TTL, admin audit log, "revoke all except one".

## Unreleased (trial/codex-keys) — 2026-07-30 (DX + panic controls)

Two features beyond the gap fixes.

### Added
- `GET /v1/whoami` (`src/mcpo/api/routers/completions.py`): a client asks what its
  credential can do — kind, scopes, status, expiry, last use, provider, active rate
  limit — instead of guessing. Works for any active key regardless of scope (new
  `ANY_SCOPE` sentinel in `src/mcpo/utils/auth.py`) and for the admin key (reports
  unlimited). Live-verified on :18006: model key returns full JSON, unauth 401.
- Panic lockdown: `ModelAPIKeyStore.revoke_all_active()` +
  `POST /chat/providers/codex-oauth/access-keys/lockdown` (admin only) revokes every
  active key AND pins enforcement on, in one call. UI button
  `settings-codex-key-lockdown` (`static/ui/index.html`, `static/ui/js/pages/settings.js`).
  Live-verified: admin lockdown revoked 1 key + enforced=true, model key got 403,
  revoked token dead afterwards.

### Verified
- `tests/test_model_api_keys.py` (+2: whoami capabilities, lockdown revoke-all-and-
  enforce). Full suite 678 passed / 15 failed (same pre-existing) / 4 skipped.
  Browser: settings page loads with no console errors, lockdown button present.

## Unreleased (trial/codex-keys) — 2026-07-30 (key lifecycle hardening)

Closes gaps found in the Codex-OAuth model-API-key feature audit. All changes in
the trial worktree.

### Added
- `ModelAPIKeyStore.set_enforcement(enabled)` (`src/mcpo/services/model_api_keys.py`)
  + endpoint `POST /chat/providers/codex-oauth/access-keys/enforcement`
  (`src/mcpo/api/routers/model_api_keys.py`). Unlatches the one-way enforcement
  flag `create_key` used to pin on forever (gap 2), and lets an admin LOCK DOWN
  `/v1` with zero keys — closing the open-by-default hole when no admin key is set
  (gap 4/6).
- `ModelAPIKeyStore.delete_key` (purge, no tombstone) + `DELETE /.../{key_id}`;
  `ModelAPIKeyStore.rotate_key` (new secret, same id/scopes, reactivates a revoked
  key) + `POST /.../{key_id}/rotate` (gap 3).
- Per-key sliding-window rate limiter, `src/mcpo/services/rate_limit.py`, wired into
  `ModelAPIKeyMiddleware` (`src/mcpo/utils/auth.py`). Off unless `MCPO_MODEL_KEY_RPM`
  > 0; in-memory (never touches the key store on the hot path); admin key is never
  limited; returns 429 + `Retry-After`/`X-RateLimit-*` headers. Per-worker window
  (effective ceiling = limit × workers), documented in the module.
- UI Rotate/Delete buttons on each key row (`static/ui/js/pages/settings.js`) with
  the new secret revealed once on rotate.
- Startup security warning when `/v1` is reachable unauthenticated
  (`src/mcpo/main.py`): names the exact lockdown call.

### Verified
- `tests/test_model_api_keys.py` (new: enforcement toggle, delete purge, rotate incl.
  revoked-key reactivation, all three endpoints + admin/read-only guards) and
  `tests/test_rate_limit.py` (window allow/block/recover, 429 for model key, admin
  exempt). Full suite: 676 passed / 15 failed (same 15 pre-existing; +9 new tests) /
  4 skipped.

### Not done (flagged, not faked)
- In-app OpenAI/Codex OAuth login (gap 1). Codex credentials still come from the
  Codex CLI writing `~/.codex/auth.json`; building a real PKCE + redirect flow is a
  separate design, not bolted on here.

## Unreleased (trial/fastmcp4) — 2026-07-30 (later)

### Added
- In-app Changelog page. New sidebar item between Settings and About; renders this
  file. Backend: `GET /_meta/changelog` (`src/mcpo/api/routers/admin.py`) serves
  the raw markdown. Frontend: `loadChangelogContent` + minimal HTML-escaping
  markdown renderer in `static/ui/js/core/api.js`, nav wiring in
  `static/ui/js/components/navigation.js`, page div in `static/ui/index.html`,
  styles in `static/ui/css/layout.css`. Verified live on :18003 in Chrome —
  page renders this entry, console clean.

### Fixed (mcp 2.0 snake_case)
- mcp 2.0 renamed model fields to snake_case; five camelCase accesses crashed the
  OpenAPI-mode app at tool registration (`'Tool' object has no attribute
  'inputSchema'`): `src/mcpo/main.py` + `src/mcpo/api/routers/tools.py`
  (`input_schema`/`output_schema`), `src/mcpo/api/routers/chat.py`
  (`input_schema`), `src/mcpo/services/runner.py` (`is_error`),
  `src/mcpo/utils/main.py` (`mime_type`). Test mocks in six files updated to the
  new attribute names. Full suite after: 15 failed (baseline was 16 — the
  `is_error` fix also repaired `test_tool_timeout_behavior`), 668 passed.

## Unreleased (trial/fastmcp4) — 2026-07-30

MCP 2026-07-28 SDK stack trial: mcp 1.28.1 → 2.0.0, fastmcp 3.4.4 → 4.0.0b1.
Trial worktree only (commits `2269c856` + `e4164428`); not merged to dev.

### Dependencies
- `pyproject.toml`: `mcp>=1.28,<2` → `mcp>=2,<3`; `fastmcp>=3.4.4,<3.5` → `fastmcp==4.0.0b1`; added `[tool.uv] prerelease = "allow"`. `uv.lock` regenerated (pulls in `mcp-types 2.0.0`, `httpx2`, `truststore`; drops `httpx-sse`).

### Changed (upgrade compatibility)
- `src/mcpo/main.py`: mcp 2.0 renamed `streamablehttp_client` → `streamable_http_client` and removed its `headers=` kwarg. Added a compat wrapper that carries headers on a pre-built httpx2 client via `create_mcp_http_client`; both call sites unchanged.
- `src/mcpo/utils/main.py`: `McpError` renamed to `MCPError` in mcp 2.0; import aliased, catch sites unchanged.
- `src/mcpo/api/routers/admin.py`: `FastMCP.as_proxy()` removed in fastmcp 4; both call sites now use `create_proxy()`.
- `tests/test_stateless_proxy.py`: monkeypatch target moved from `FastMCP.as_proxy` to `fastmcp.server.create_proxy`.
- `mcpo.json` (untracked, local): `time` server pinned `uvx --with "mcp<2" mcp-server-time` — upstream `mcp-server-time` crashes on import against mcp 2.x (its own env resolves latest mcp). This breakage is independent of the upgrade and also affects the current production stack.

### Verified
- Proxy suites: 69/69 pass on the new stack (`test_stateless_proxy`, `test_hot_reload`, `test_proxy_auth`, `test_oauth_self_hosted`, `test_mcp_middlewares`, `test_mcp_spec_2026`).
- Full suite: 667 passed / 16 failed / 3 skipped — the identical 16 fail on the old stack (pre-existing, not upgrade-caused).
- Live smoke on :18001 against real backends: legacy initialize + tools/list (desktop-commander, 26 tools; session id emitted), 2026-07-28 sessionless `server/discover` + `tools/list` (no session header; requires `MCP-Protocol-Version` + `mcp-method` headers and the `_meta` protocol envelope), state-disabled mounts return zero tools, pinned time server round-trips a real `get_current_time` call.

### Known gaps
- fastmcp 4.0.0b1 is a beta; elicitation forwarding through the proxy (PrefectHQ/fastmcp#3169, closed via #3172) is present in source but not live-tested here.

## Unreleased (feat/phase1-baseline)

### Architecture Consolidation (January 2025)
- 🏗️ **Unified Port Architecture**: Consolidated all services to optimize performance on M4 Mini Pro hardware
  - **Port 8000**: MCPO unified server (admin + transcription + UI)
  - **Port 8001**: MCPP proxy (MCP streamable-http, unchanged)
  - **Port 2638**: External Whisper server (batch STT, unchanged)
- 🚀 **Real-time Transcription Integration**: Added comprehensive transcription features on main port
  - WebSocket endpoint: `/_transcribe/ws` for bi-directional audio streaming
  - Admin endpoints: `/_transcribe/admin/endpoints` for per-user token management
  - Health monitoring: `/_transcribe/health` proxying upstream Whisper server status
  - Feature-gated by `TRANSCRIBE_FEATURE_ENABLED=true` environment variable
- 🎨 **Enhanced UI Components**: New "Realtime TX" page with playground, endpoint management, and monitoring
  - Test playground with WebSocket controls and audio transcription output
  - Per-user endpoint/token generation and management table
  - Real-time connection status and upstream health monitoring
  - Integrated logs tab for transcription activity tracking
- 📈 **Performance Optimization**: Leveraged M4 Mini Pro capabilities (64GB unified memory + 16-core GPU)
  - Single-port architecture eliminates CORS complexity and auth fragmentation
  - Reduced network latency and simplified deployment model
  - Optimized for 100+ concurrent transcription sessions with local ML inference
- 🔧 **Simplified Deployment**: Updated `start.bat` to reflect unified architecture
  - Removed redundant 8003 transcription proxy
  - Consolidated environment variable configuration
  - Streamlined two-process deployment (8000 + 8001)

### Recovery Notes (August 2025)
- ✅ **Complete System Recovery**: Successfully recovered from git revert data loss incident
- 🔍 **Forensic Analysis**: Identified and preserved latest UI version (1577 lines) and evolved backend (1778 lines) 
- 🧹 **Workspace Cleanup**: Organized recovery materials and removed failed modular upgrade remnants
- ✅ **Full Verification**: 49/49 test suite passing, all MCP server connections operational
- 🚀 **Production Ready**: System fully restored and verified working with all 4 MCP servers (Management, Perplexity, Time, Playwright)

### Critical Bug Fixes (January 2025)
- 🔧 **State Persistence Recovery**: Fixed circular import issue preventing state loading on startup
  - Moved state loading to occur after service initialization
  - Added proper error handling and graceful fallbacks
  - Integrated state saving into lifespan cleanup for persistence across restarts
- 🔗 **Complete OpenAPI Aggregation Implementation**: Fully implemented missing `/_meta/aggregate_openapi` endpoint
  - Smart caching with automatic invalidation triggers
  - Schema collision detection and resolution with server-prefix naming
  - Real-time filtering based on server and tool enable/disable states
  - Integrated cache invalidation on reload, server enable/disable, and tool enable/disable operations
  - Production-ready for external integrations (Open WebUI, SDK generators, API documentation)
- 🛠️ **Backend Production Readiness**: All critical functionality now operational and tested
  - Thread-safe service architecture fully functional
  - Robust error handling and logging implemented
  - Ready for frontend deployment with full backend support

### Added
- 🎛️ **Real-time Log Monitoring & UI Management**: Complete management interface at `/ui` with live server logs, configuration editing, and comprehensive tool visibility
- 🔧 **Dynamic Configuration Management**: Edit `mcpo.json` directly through the UI with real-time validation, backup creation, and instant server reload
- 📦 **Python Dependency Management**: UI-based `requirements.txt` editing with automatic `pip install` on server startup and reload
- 🛠️ **Self-Managed Internal Tools**: MCPO now exposes its own management capabilities as discoverable MCP tools at `/mcpo/openapi.json`
  - `install_python_package`: Dynamically install Python packages via pip
  - `get_config`: Retrieve current `mcpo.json` configuration
  - `post_config`: Update configuration and trigger server reload
  - `get_logs`: Retrieve last 20 server log entries
- 📊 **Enhanced Log Buffer System**: In-memory log capture with thread-safe access for real-time UI display via `/_meta/logs`
- 🔄 **Advanced Configuration Endpoints**: Complete CRUD operations for configuration management
  - `/_meta/config/content`: Get formatted `mcpo.json` content
  - `/_meta/config/save`: Save and validate configuration with backup and reload
  - `/_meta/requirements/content`: Get `requirements.txt` content
  - `/_meta/requirements/save`: Save dependencies and trigger installation
- 🎨 **Modern Configuration UI**: Single-page interface with tabs for servers, logs, and configuration editing
- 🔍 **Tool Call Execution Logging**: Comprehensive logging of MCP tool calls with success/failure tracking and detailed error reporting
- 🏗️ **Internal MCP Server Architecture**: Self-hosted tools using mock MCP sessions for seamless integration with existing tool discovery
- `/healthz` endpoint and health snapshot
- Pydantic config models (`AppConfig`, `ServerConfig`)
- Global reload lock for atomic config reloads
- `--tool-timeout` CLI option (default 30s)
- `--structured-output` flag (experimental) adding typed collection envelope
- Enum and min/max length/number constraint exposure in dynamic models
- MCP protocol version header injection (`MCP-Protocol-Version: 2025-06-18`)
- Unified success + error envelope helpers and global HTTP/validation handlers
- Tool-level unified error envelope (consistent `{ok:false,error:{...}}` shape)
- Tests for structured output and tool error envelope
- Enforced per-invocation tool timeout with async cancellation (`asyncio.wait_for`)
- Per-request timeout override via `X-Tool-Timeout` header or `?timeout=` query param
- Hard upper bound `--tool-timeout-max` with validation and error envelope
- Meta endpoints: `/_meta/servers`, `/_meta/servers/{server}/tools`, `/_meta/config`
- Dynamic config reload endpoint `/_meta/reload` and per-server reinit `/_meta/reinit/{server}`
- Server & tool enable/disable endpoints (403 enforcement for disabled tools)
- In-browser settings UI at `/mcp` with theme toggle, >40 tool warning, expandable server panels
- Add / remove server endpoints (config mode persistence) and modal (Git analysis stub + manual path)
- Open config action (vscode:// deep link) from UI
- `--read-only` flag to disable all mutating management endpoints for safer embedding/distribution
- Versioned, atomic state persistence for server/tool enable flags (`*_state.json` with temp-file replace)
- 🔗 **Aggregated OpenAPI Specification**: New `/_meta/aggregate_openapi` endpoint that provides a unified OpenAPI 3.1 spec combining all enabled MCP servers
  - Smart caching with automatic invalidation when servers/tools are modified
  - Schema collision detection and resolution with server-prefix naming
  - Reference path rewriting for proper component linking
  - Real-time filtering based on server and tool enable/disable states
  - Optimized for integration with external tools like Open WebUI, SDK generators, and API documentation
- 🧪 **Live HTTP Integration Tests**: Added `test_real_tool_execution.py` exercising real server endpoints (no in-process shutdown) including success, error, timeout, and validation cases.
- 🧪 **Protocol Version Header Test Suite**: Added `test_protocol_version_header.py` covering presence/absence of `MCP-Protocol-Version` header and log warning capture.
- 🔐 **Hardening (Phase 1 Security Fixes)**: Session health checks, atomic file writes, path traversal prevention, package name validation, JSON size limits, thread-safe log buffering.
- 🔀 **Namespaced Tool Routing Pattern**: Standardized `/server/tool` path usage (e.g. `/time/get_current_time`) and aligned tests.

### Changed
- Correct README Python version to 3.11+
- Cleanup duplicate imports
- Removed unused JWT/passlib dependencies from default install (simplifies surface area; API key model only in phase 1)

### Planned / Pending
- Expanded structured output (streaming, richer resource metadata)
- Additional tests for image/resource items & timeout behavior
- 🏷️ **Enhanced OpenAPI Tagging**: Improved server-based tagging for better UI organization
- 🧪 **Extended Aggregation Test Coverage**: Enhanced test coverage for edge cases and complex scenarios
- 🤐 **Protocol Warn Optimization**: Auto-suppress benign missing-header warnings when internally injected
- 🔄 **Optional Root Proxy Shortcuts**: Flat proxy routes (e.g. `/get_current_time`) if required by downstream clients
- 🎨 **Frontend Modularization**: Break up monolithic UI files into maintainable components

---
Historical entries are maintained upstream; this fork annotates divergence points below.

## [0.0.17] - 2025-07-22

### Added

- 🔄 **Hot Reload Support for Configuration Files**: Added \`--hot-reload\` flag to watch your config file for changes and dynamically reload MCP servers without restarting the application—enabling seamless development workflows and runtime configuration updates.
- 🤫 **HTTP Request Filtering for Cleaner Logs**: Added configurable log filtering to reduce noise from frequent HTTP requests, making debugging and monitoring much clearer in production environments.

### Changed

- ⬆️ **Updated MCP Package to v1.12.1**: Upgraded MCP dependency to resolve compatibility issues with Pydantic and improve overall stability and performance.
- 🔧 **Normalized Streamable HTTP Configuration**: Streamlined configuration syntax for streamable-http servers to align with MCP standards while maintaining backward compatibility.

## [0.0.16] - 2025-07-02

### Added

- 🔁 **Enhanced Endpoint Support for Arbitrary Return Types**: Endpoints can now return any JSON-serializable value—removing limitations on tool outputs and enabling support for more diverse workflows, including advanced data structures or dynamic return formats.
- 🪵 **Improved Log Clarity with Streamlined Print Trace Output**: Internal logging has been upgraded with more structured and interpretable print traces, giving users clearer visibility into backend tool behavior and execution flow—especially helpful when debugging multi-agent sequences or nested toolchains.

### Fixed

- 🔄 **Resolved Infinite Loop Edge Case in Custom Schema Inference**: Fixed a bug where circular references ($ref) caused schema processing to hang or crash in rare custom schema setups—ensuring robust and reliable auto-documentation even for deeply nested models.

## [0.0.15] - 2025-06-06

### Added

- 🔐 **Support for Custom Headers in SSE and Streamable Http MCP Connections**: You can now pass custom HTTP headers (e.g., for authentication tokens or trace IDs) when connecting to SSE or streamable_http servers—enabling seamless integration with remote APIs that require secure or contextual headers.
- 📘 **MCP Server Instructions Exposure**: mcpo now detects and exposes instructions output by MCP tools, bringing descriptive setup guidelines and usage help directly into the OpenAPI schema—so users, UIs, and LLM agents can better understand tool capabilities with zero additional config.
- 🧪 **MCP Exception Stacktrace Printing During Failures**: When a connected MCP server raises an internal error, mcpo now displays the detailed stacktrace from the tool directly in the logs—making debugging on failure dramatically easier for developers and MLops teams working on complex flows.

### Fixed

- 🧽 **Corrected Handling of Underscore Prefix Parameters in Pydantic Modes**: Parameters with leading underscores (e.g. _token) now work correctly without conflict or omission in auto-generated schemas—eliminating validation issues and improving compatibility with tools relying on such parameter naming conventions.

## [0.0.14] - 2025-05-11

### Added

- 🌐 **Streamable HTTP Transport Support**: mcpo now supports MCP servers using the Streamable HTTP transport. This allows for more flexible and robust communication, including session management and resumable streams. Configure via CLI with '--server-type "streamable_http" -- <URL>' or in the config file with 'type: "streamable_http"' and a 'url'.

## [0.0.13] - 2025-05-01

### Added

- 🧪 **Support for Mixed and Union Types (anyOf/nullables)**: mcpo now accurately exposes OpenAPI schemas with anyOf compositions and nullable fields.
- 🧷 **Authentication-Required Docs Access with --strict-auth**: When enabled, the new --strict-auth option restricts access to both the tool endpoints and their interactive documentation pages—ensuring sensitive internal services aren’t inadvertently exposed to unauthenticated users or LLMs.
- 🧬 **Custom Schema Definitions for Complex Models**: Developers can now register custom BaseModel schemas with arbitrary nesting and field variants, allowing precise OpenAPI representations of deeply structured payloads—ensuring crystal-clear docs and compatibility for multi-layered data workflows.
- 🔄 **Smarter Schema Inference Across Data Types**: Schema generation has been enhanced to gracefully handle nested unions, nulls, and fallback types, dramatically improving accuracy in tools using variable output formats or flexible data contracts.

## [0.0.12] - 2025-04-14

### Fixed

- ⏳ **Disabled SSE Read Timeout to Prevent Inactivity Errors**: Resolved an issue where Server-Sent Events (SSE) MCP tools would unexpectedly terminate after 5 minutes of no activity—ensuring durable, always-on connections for real-time workflows like streaming updates, live dashboards, or long-running agents.

## [0.0.11] - 2025-04-12

### Added

- 🌊 **SSE-Based MCP Server Support**: mcpo now supports SSE (Server-Sent Events) MCP servers out of the box—just pass 'mcpo --server-type "sse" -- http://127.0.0.1:8001/sse' when launching or use the standard "url" field in your config for seamless real-time integration with streaming MCP endpoints; see the README for full examples and enhanced workflows with live progress, event pushes, and interactive updates.

## [0.0.10] - 2025-04-10

### Added

- 📦 **Support for --env-path to Load Environment Variables from File**: Use the new --env-path flag to securely pass environment variables via a .env-style file—making it easier than ever to manage secrets and config without cluttering your CLI or exposing sensitive data.
- 🧪 **Enhanced Support for Nested Object and Array Types in OpenAPI Schema**: Tools with complex input/output structures (e.g., JSON payloads with arrays or nested fields) are now correctly interpreted and exposed with accurate OpenAPI documentation—making form-based testing in the UI smoother and integrations far more predictable.
- 🛑 **Smart HTTP Exceptions for Better Debugging**: Clear, structured HTTP error responses are now automatically returned for bad requests or internal tool errors—helping users immediately understand what went wrong without digging through raw traces.

### Fixed

- 🪛 **Fixed --env Flag Behavior for Inline Environment Variables**: Resolved issues where the --env CLI flag silently failed or misbehaved—environment injection is now consistent and reliable whether passed inline with --env or via --env-path.

## [0.0.9] - 2025-04-06

### Added

- 🧭 **Clearer Docs Navigation with Path Awareness**: Optimized the /docs and /[tool]/docs pages to clearly display full endpoint paths when using mcpo --config, making it obvious where each tool is hosted—no more guessing or confusion when running multiple tools under different routes.
- 🛤️ **New --path-prefix Option for Precise Routing Control**: Introduced optional --path-prefix flag allowing you to customize the route prefix for all mounted tools—great for integrating mcpo into existing infrastructures, reverse proxies, or multi-service APIs without route collisions.
- 🐳 **Official Dockerfile for Easy Deployment**: Added a first-party Dockerfile so you can containerize mcpo in seconds—perfect for deploying to production, shipping models with standardized dependencies, and running anywhere with a consistent environment.

## [0.0.8] - 2025-04-03

### Added

- 🔒 **SSL Support via '--ssl-certfile' and '--ssl-keyfile'**: Easily enable HTTPS for your mcpo servers by passing certificate and key files—ideal for securing deployments in production, enabling encrypted communication between clients (e.g. browsers, AI agents) and your MCP tools without external proxies.

## [0.0.7] - 2025-04-03

### Added

- 🖼️ **Image Content Output Support**: mcpo now gracefully handles image outputs from MCP tools—returning them directly as binary image content so users can render or download visuals instantly, unlocking powerful new use cases like dynamic charts, AI art, and diagnostics through any standard HTTP client or browser.

## [0.0.6] - 2025-04-02

### Added

- 🔐 **CLI Auth with --api-key**: Secure your endpoints effortlessly with the new --api-key option, enabling basic API key authentication for instant protection without custom middleware or external auth systems—ideal for public or multi-agent deployments.
- 🌐 **Flexible CORS Access via --cors-allow-origins**: Unlock controlled cross-origin access with the new --cors-allow-origins CLI flag—perfect for integrating mcpo with frontend apps, remote UIs, or cloud dashboards while maintaining CORS security.

### Fixed

- 🧹 **Cleaner Proxy Output**: Dropped None arguments from proxy requests, resulting in reduced clutter and improved interoperability with servers expecting clean inputs—ensuring more reliable downstream performance with MCP tools.
