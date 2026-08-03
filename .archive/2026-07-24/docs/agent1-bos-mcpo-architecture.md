# Agent 1: BOS-MCPO Architecture, as understood from session context

Written 2026-07-23 from context memory only, no fresh code inspection. Each claim is tagged:
[V] verified this session (file read, command run, or wire test), [D] read from a repo doc,
[S] second-hand (another agent's diary record or a sub-agent report), [A] assumption.

## System purpose

mcpo is a local MCP aggregation and exposure layer. It spawns/connects a fleet of MCP
servers from one config file and exposes them over multiple surfaces:

- OpenAPI/REST endpoints per server (the original mcpo function). [A: name and repo shape imply it; not directly exercised this session]
- An aggregate MCP endpoint plus per-server MCP endpoints, OAuth-gated, tunneled publicly. [D]
- A local admin UI and `_meta` REST API for config, skills, logs, code-mode. [V]
- A chat harness (chat sessions, code mode, model catalog, providers) inside the same app. [A: inferred from file names in git status, e.g. services/chat_sessions.py, providers/, code_mode; internals not read]

## Processes and ports

| Process | Port | Role |
|---|---|---|
| Admin app, `python -m mcpo serve` | :8000 | UI at /ui, `_meta` REST routers. Local only. [D] |
| Plain proxy, `python -m mcpo.proxy` (start.bat) | :8001 | For OpenWebUI and similar local clients. [D] |
| OAuth proxy, `python -m mcpo.proxy --oauth` | 127.0.0.1:8351 | Public MCP surface, tunneled. [D] |
| cloudflared | n/a | Windows service, tunnel id 4979962f-..., hostname dev.ai.lighting. [D] |
| Gateway POC container | host 127.0.0.1:<site_id> → container 8351 | Site-scoped dockerized mcpo instance for the BOS vendor path. [S] |

## Public surfaces

### Dev tunnel (dev.ai.lighting) [D: docs/OAUTH_TUNNEL_SETUP.md, read this session]

- Base `https://dev.ai.lighting`, aggregate MCP at `/mcp` (all enabled servers, ~44 tools at doc time), per-server at `/<name>/` (trailing slash required).
- Self-hosted OAuth 2.1 (`src/mcpo/utils/oauth_self_hosted.py`, `KeyGatedOAuthProvider`): consent page where the human pastes the mcpo API key, PKCE public clients via DCR, tokens persisted to `.mcpo_oauth_state.json` so restarts do not force re-auth.
- Load-bearing fixes recorded in that doc: forced public clients (strip client_secret), CSP `form-action 'self' https:`, roots answered locally as `[]`, tool-filter middleware removed from the OAuth path (ASGI corruption), double-submit guard on consent, tunnel token reinstall.
- IMPORTANT consequence [V]: the tunnel exposes MCP tools only. The `_meta` REST API is not reachable through it. Anything a remote harness must trigger has to be an MCP tool, not an endpoint.

### BOS vendor relay (<site_id>.ai.lighting) [D: docs/bos-tunnel-doc, read in part]

Separate system from the dev tunnel. Outbound-only application relay, not a VPN:
client makes an outbound WSS connection to the vendor; vendor routes
`https://<site_id>.ai.lighting/mcp` through its broker down the existing WSS session to a
local FastMCP on 127.0.0.1:7087, which fronts the site gateway on :7085 and the Building OS
tool catalogue (8 read-only tools without a pinned BR, 19 with, 35 canonical). Site identity:
five-digit `site_id` derived from the router serial, used as both hostname label and loopback
routing port. Doc states it is NOT operational end-to-end on this machine and the public
Cloudflare path is unproven.

### Gateway POC (site-scoped mcpo) [S: another agent's record in .claude/diary-2026-07-23.md; not verified by me]

- One env var `BOS_VENDOR_SITE_ID` (five digits, 10000..65535, validated at startup via `vendor_site_port_from_env()`) drives: compose project suffix, host-published port, public hostname label, and backend vendor hostname label. Container listens on fixed 8351.
- Public OAuth base `https://<site_id>.gateway.ai.lighting`; MCP backend `https://<site_id>.ai.lighting/mcp`.
- `${VAR}` interpolation was extended to `mcpServers.<name>.url` (previously env/headers only) in both the shared config utility and the proxy runtime.
- Full test suite reported passing after those edits (640 passed, 4 skipped).

## Config model

- File: `mcpo.json`. Two accepted shapes, normalized by `normalize_config_shape` [V]:
  `{"mcpServers": {...}}` or `{"config": {"mcpServers": {...}}, "server": ...}`. The live file uses the nested shape [V].
- `${VAR}` interpolation: env and headers values [V]; url values added by the gateway POC work [S].
- Hot reload paths [V]:
  - Admin app: watchdog `ConfigWatcher` on the config file (repo convention, `utils/config_watcher.py`).
  - Plain proxy: same `ConfigWatcher` mechanism, in-place rebuild (`proxy.py` ~816-845).
  - OAuth proxy: uvicorn directory watch, process restart, ONLY when launched with `--hot-reload` (`proxy.py` ~553-582).
- `_meta` REST (admin app only) [V]: `/config/save` (validate JSON, write, reload in place, remount), `/config/mcpServers/save`, `/reload`, `/reinit/{server}`, enable/disable per server and per tool, logs, env, skills, cli-packages, skill-packages, code-mode.
- Roots protocol: deliberately stubbed. The proxy answers `roots/list` with `[]` because ChatGPT/Claude remote clients do not implement roots and a forwarded request tears the session down (`proxy.py` ~413-429) [V]. Runtime re-scoping via roots is therefore unavailable behind mcpo; workspace switching is done by config rewrite + hot reload instead.

## Workspace model (built 2026-07-23) [V: built and wire-tested this session]

Goal: Claude Code style workspace lock for servers behind mcpo, switchable by the model.

- New top-level config section:
  `"workspace": { "active": <path>, "allowlist": [<parent dirs>], "targets": { "<server>": {"kind":"arg","position":"last"} | {"kind":"env","name":"<ENV>"} } }`
- New stdio server `tools/workspace-manager/workspace_manager.py` (low-level `mcp.server` API, matching the `tools/cli-shim` convention). Tools:
  - `get_workspace()` returns active/allowlist/targets.
  - `set_workspace(path)`: absolute + exists + directory + realpath containment inside an allowlist parent (Path.is_relative_to on resolved paths, immune to `..` and sibling-prefix tricks, case-insensitive on Windows). Two-phase: validate every target, then rewrite (last arg or named env var), update `workspace.active`, atomic write (temp file + os.replace). Bad target aborts with zero changes. Written paths are resolved canonical form, forward slashes.
- Propagation: the config write triggers the hot-reload machinery in whichever mcpo processes are running; affected servers restart scoped to the new root. Over the tunnel this requires the OAuth proxy to be running with `--hot-reload`.
- Because it is an MCP tool, it works over every surface including the tunnel. A REST endpoint would not.
- Status: code + 21 unit tests + end-to-end stdio test all pass. NOT yet registered in mcpo.json; paste-ready snippets delivered to Dan. Config registration is Dan's action because every save hot-reloads the live stack.
- Known unverified: concurrent write race between the tool and a simultaneous UI save (expected last-write-wins, atomic replace prevents corruption).

## Memory / diary layer (decided, not yet wired)

- Manual layer today: dated markdown diaries in `.claude/` (`diary-YYYY-MM-DD.md`), shared by agents working this repo. [V]
- Chosen DB-backed candidate: `memory-journal-mcp` 8.0.1 (neverinfamous). Windows test PASSED [S: sub-agent run, artifacts in scratchpad]: prebuilt native modules (better-sqlite3, sqlite-vec), install 12s, handshake 1.3s, FTS + date-range + semantic search all exercised with real positive and negative results. DB location per instance via `DB_PATH` env or `--db`, proven by writing into a `workspace/.memory/` folder.
- Integration design: register it with a pinned version, `DB_PATH` env, tool filter cutting 73 tools to a starter set, and add `{"kind":"env","name":"DB_PATH"}` to `workspace.targets` so `set_workspace` repoints the diary to `<workspace>/.memory/`.
- Gotchas on record: bare `npm install` resolved a stale v3.1.5 twice despite dist-tag latest 8.0.1, so the version must be pinned; engine field wants Node >= 24 while the machine runs 22.22 (works today, durability unverified); npm audit reports 2 moderate + 5 high; use `create_entry_minimal` (the full `create_entry` is GitHub-project-shaped).

## Current server stack in mcpo.json [V: file read 2026-07-22] with vet status

| Entry | Status from this session's research |
|---|---|
| desktop-commander | Keep. Top pick for shell + string-replace edit + ripgrep search (8.7k stars, active). |
| text-editor (uvx mcp-text-editor) | Keep. tumf's hash-conflict-detecting editor, the #2 edit pick. |
| github (official, docker + gh token) | Keep. Top pick. |
| memory (reference server-memory) | Keep, secondary. |
| ai-cli-mcp | Keep. Vetted: mkXultra/ai-cli-mcp, active, multi-CLI sub-agent runner (Claude/Codex/Gemini) with session_id resume. |
| claude-code-mcp (@steipete) | DROP recommended. Archived May 2026. Its multi-turn role is covered by ai-cli-mcp. |
| codex-subagent (codex-as-mcp) | Replace recommended with first-party `codex mcp-server` (verified working on this machine). |
| perplexity | Search only, no raw URL fetch. Gap: add duckduckgo-mcp-server (fetch+search, no key) if parity wanted. |
| workspace-mcp (:7733 streamable-http) | Google Workspace (gmail/drive/calendar), verified live. Name collision, nothing to do with codebase workspaces. |
| filesystem (building-os), workspaceFiles (repos/max) | Reference filesystem servers pinned to roots; workspaceFiles is the first `workspace.targets` candidate (root is its last arg). |
| chrome-devtools, playwright, time, xero, xero-code-mode, kicad, kicad-pro-code-mode, kicad-cli, powershell-mcp, x, echo-server | Not in scope of this session's research; unvetted here. |

## Sub-agent / harness findings on record (research, 2026-07-22/23 diaries)

- No first-party Claude Code equivalent of `codex mcp-server` exists; `claude mcp serve` is raw tools only (28 tools, schemas identical to Claude Code's own). Anthropic's endorsed path for a hosted multi-turn agent is self-hosting the Agent SDK with a SessionStore.
- For a GPT-model harness: match the edit tool to the model's training. GPT models are trained on OpenAI's V4A `apply_patch`; `agynio/codex-tools-mcp` wraps OpenAI's actual apply-patch crate (3 stars, vet before trust). Claude-style old_string/new_string is what desktop-commander and the filesystem reference server speak.
- Search upgrades if wanted: johnhuang316/code-index-mcp (closest Glob+Grep 1:1), probelabs/probe (ripgrep + tree-sitter AST blocks), ast-grep-mcp (structural, complement only).

## Open items

1. Dan to paste (or approve one edit adding) workspace-manager entry + workspace section + memory-journal entry into mcpo.json.
2. `--hot-reload` must be added to the OAuth proxy run command or tunnel clients will not see workspace switches.
3. Optional stack cleanups: drop archived claude-code-mcp, swap codex-as-mcp for `codex mcp-server`, add a URL-fetch server.
4. Gateway POC and BOS vendor relay end-to-end proof remains open per their own records; not this agent's verified territory.
