# MCPO Work-in-Progress Intent Map

> **Generated**: Codebase audit session  
> **Scope**: `D:\mcpo\src\mcpo\` — all modules, active and WIP  
> **Purpose**: Document every WIP module's intent, trace the architectural evolution, and map what replaces what.

---

## 1. Architectural Evolution

### Phase 0 — Original MCPO (Base Layer)

MCPO started as a **basic MCP-to-OpenAPI proxy**: take MCP tool servers defined in `mcpo.json`, connect via stdio/SSE/Streamable HTTP, introspect their tools, and auto-generate OpenAPI REST endpoints for each tool. One FastAPI app, one purpose.

**What survived from Phase 0 (still active in `main.py`):**

| Component | Location | Role |
|-----------|----------|------|
| `load_config()` | `main.py:155` | Parse `mcpo.json`, validate server entries |
| `create_sub_app()` | `main.py:178` | Build a FastAPI sub-app per MCP server |
| `create_dynamic_endpoints()` | `main.py:407` | Introspect MCP tools → auto-generate POST endpoints |
| `lifespan()` | `main.py:478` | Manage MCP client sessions (stdio/SSE/streamable-http) |
| `mount_config_servers()` | `main.py:256` | Mount all config-defined servers under `/{server_name}` |
| `get_tool_handler()` | `utils/main.py:420` | Build the actual endpoint function per tool |
| `_process_schema_property()` | `utils/main.py:106` | Recursive MCP JSON Schema → Pydantic model conversion |
| `get_model_fields()` | `utils/main.py:383` | Schema → Pydantic field mapping |
| `process_tool_response()` | `utils/main.py:49` | Convert MCP `CallToolResult` to JSON |

### Phase 1 — Admin & State Management

The app gained a web UI and management endpoints. `main.py` grew from ~400 lines to 2057 lines.

**Additions:**
- `/_meta/*` inline endpoints (servers, tools, config, logs, reload, enable/disable, metrics)
- `StateManager` (`services/state.py`) — persistent enable/disable for servers, tools, providers, models, and favorites via `mcpo_state.json`
- `LogManager` (`services/logging.py`) + `BufferedLogHandler` (`services/logging_handlers.py`) — UI-facing log capture
- `MetricsAggregator` (`services/metrics.py`) — call/error tracking
- `RunnerService` (`services/runner.py`) — centralized tool execution with timeouts
- Internal MCPO management server (`/mcpo` mount) — self-management MCP tools
- Static UI mount (`/ui`)
- Hot reload via `ConfigWatcher` (`utils/config_watcher.py`)

### Phase 2 — Proxy Server (Dual Process)

A second server was added: `proxy.py` (port 8001). It exposes the native MCP Streamable HTTP protocol for clients that speak MCP natively (e.g., Claude Desktop, Cursor).

**`proxy.py` (455 lines, ACTIVE):**
- Reads `mcpo.json`, creates `FastMCP.as_proxy()` for aggregate and per-server endpoints
- Applies `MCPToolFilterMiddleware` to respect enable/disable states
- Has its own `/_meta/logs` router for proxy-side log viewing
- Runs independently from main.py (separate uvicorn instance)

### Phase 3 — Provider Layer (All-in-One OpenWebUI Proxy)

The project expanded from "MCP tool proxy" to "all-in-one OpenWebUI backend" — meaning it now needs to expose `/v1/chat/completions` and `/v1/models` endpoints that route to multiple LLM providers.

**Active provider infrastructure:**

| Module | Lines | Status | Used By |
|--------|-------|--------|---------|
| `providers/anthropic.py` | 720 | Built, partially wired | `providers/minimax.py` only |
| `providers/openai.py` | 864 | Built, NOT wired | Nothing (completions.py has inline duplicate) |
| `providers/google.py` | 854 | Built, wired to chat.py | `chat.py` (via `GoogleClient`) |
| `providers/openrouter.py` | 270 | Active, catch-all | `chat.py` (fallback for all non-Google/non-MiniMax) |
| `providers/minimax.py` | ~110 | Rewritten, wired | `chat.py` (via `MiniMaxClient`) |

**Two active API routers consuming providers:**

| Router | Lines | Purpose |
|--------|-------|---------|
| `api/routers/chat.py` | 1242 | Session-based chat with tool execution loop |
| `api/routers/completions.py` | 1061 | Stateless OpenAI-compatible `/v1/chat/completions` endpoint |

### Phase 4 — Modular Extraction (In Progress)

The intent is to decompose `main.py`'s 2057-line monolith into proper packages:

- **`models/`** → Typed Pydantic domain models replacing ad-hoc dicts
- **`services/registry.py`** → Config loading/CRUD replacing `main.py:load_config()`
- **`services/hot_reload.py`** → Watchdog service replacing `utils/config_watcher.py`
- **`api/routers/meta.py`** → Extract `/_meta` endpoints from `main.py`
- **`api/routers/tools.py`** → Extract `create_dynamic_endpoints` from `main.py`
- **`api/routers/health.py`** → Standalone health endpoint
- **`app/factory.py`** → App factory pattern for `build_main_app()`

---

## 2. WIP Module Inventory

### 2.1 `models/` Package — Typed Domain Layer

**Status**: Complete, not yet consumed  
**Intent**: Replace all ad-hoc `Dict[str, Any]` with Pydantic v2 models  
**Files**: 4

#### `models/domain.py` (183 lines)

**What it provides:**
- `ServerType` enum: `STDIO`, `SSE`, `STREAMABLE_HTTP`
- `ServerState` enum: `CONNECTED`, `DISCONNECTED`, `ERROR`, `INITIALIZING`
- `ToolState` enum: `ENABLED`, `DISABLED`
- `ServerInfo` dataclass: name, description, version
- `ToolInfo` dataclass: name, description, input_schema, output_schema, enabled flag
- `ServerConfiguration` Pydantic model: name, type, command, args, url, env, headers, enabled — with validators for type/command/url consistency
- `ServerRuntimeState`: wraps config + state + tools + session metrics; provides `enable_tool()`, `disable_tool()`, `set_connected()`, `get_enabled_tools()`
- `LogEntry`: timestamp, level, message, source, category, data
- `SessionMetrics`: tracking calls, errors, total/avg latency per session; `record_call()`, `get_summary()`

**What it replaces:**
- Raw dicts in `main.py:create_sub_app()` (server config)
- Raw dicts in `services/state.py` (server/tool states)
- Inline health snapshot dict in `main.py:_health_state`
- Log entry dicts in `services/logging.py`

**Wiring needed**: Import `ServerConfiguration` where `validate_server_config()` is called; use `ServerRuntimeState` in lifespan and meta endpoints; use `LogEntry` in `LogManager`.

#### `models/config.py` (36 lines)

**What it provides:**
- `ServerConfig`: name (str), type (ServerType), command/args (Optional), url (Optional), env/headers (Optional Dict) — with `model_validator` enforcing: stdio needs command, SSE/streamable-http needs url
- `AppConfig`: servers (List[ServerConfig]) with unique-name validator

**What it replaces:**
- `main.py:validate_server_config()` (manual validation)
- Raw dict handling in `load_config()`

#### `models/responses.py` (122 lines)

**What it provides:**
- `ErrorDetail`: message, code (Optional), data (Optional), timestamp
- `ApiResponse` (base): ok bool
- `ErrorResponse(ApiResponse)`: ok=False, error (ErrorDetail)
- `SuccessResponse(ApiResponse)`: ok=True, data (Optional), message (Optional)
- `CollectionResponse(SuccessResponse)`: items list, total int
- `ServerStatus`: name, connected, type, basePath, enabled
- `ToolStatus`: name, enabled
- `HealthStatus`: status, generation, lastReload, servers dict
- `MetricsData`: servers, tools, calls, errors, perTool
- `ServerMetrics(SuccessResponse)`: metrics (MetricsData)
- Backward-compat functions: `create_error_envelope()`, `create_success_envelope()`, `error_envelope()`

**What it replaces:**
- `main.py:error_envelope()` (inline function)
- `utils/main.py:create_error_envelope()` and `create_success_envelope()`
- Ad-hoc response dicts in every `/_meta` endpoint
- Inconsistent error response shapes across routers

#### `models/__init__.py` (32 lines)

Clean re-exports with `__all__`. Ready to import.

---

### 2.2 `services/registry.py` — Server Registry Service (135 lines)

**Status**: Complete, not yet consumed  
**Intent**: Centralized config CRUD replacing scattered config loading in `main.py`

**What it provides:**
- `ServerConfig` (wrapper class): wraps raw config dict with `.type`, `.command`, `.url`, `.enabled` properties
- `ServerRegistry`:
  - `load_config(path)` → parse `mcpo.json`, store servers
  - `get_server_config(name)` → return `ServerConfig`
  - `add_server(name, config)` → add new server + persist
  - `remove_server(name)` → remove server + persist
  - `update_server(name, config)` → update server + persist
  - `save_config()` → write back to disk
- `get_server_registry()` → global singleton

**What it replaces:**
- `main.py:load_config()` — parsing `mcpo.json`
- `main.py:add_server()` / `remove_server()` endpoints — direct file I/O
- Scattered `json.load()`/`json.dump()` calls across config endpoints

**Wiring needed**: Import in `build_main_app()` and all `/_meta/config/*` endpoints. Would clean up ~200 lines of config handling from `main.py`.

---

### 2.3 `services/hot_reload.py` — Hot Reload Service (171 lines)

**Status**: Complete, not yet consumed  
**Intent**: Replace `utils/config_watcher.py` with a more feature-complete implementation

**What it provides:**
- `ConfigFileHandler(FileSystemEventHandler)`: handles modified/moved/created events for config file
- `HotReloadService`:
  - `start_watching(config_path, callback)` → watchdog observer
  - `stop_watching()` → clean shutdown
  - Built-in debouncing (configurable delay)
  - Async callback support with proper event loop integration
  - Error handling for invalid JSON, missing files
- `get_hot_reload_service()` → global singleton

**What it replaces:**
- `utils/config_watcher.py:ConfigWatcher` (162 lines) — same watchdog pattern but less robust

**Key differences from current `ConfigWatcher`:**
- Singleton pattern (vs per-instantiation)
- Cleaner debounce implementation
- Explicit start/stop lifecycle methods
- Better error categorization

---

### 2.4 `api/routers/meta.py` — Meta Endpoints Router (920 lines)

**Status**: Complete implementation, mounted but imports functions from `main.py`  
**Intent**: Extract ALL `/_meta/*` endpoints out of `main.py` into a proper APIRouter

**What it provides (endpoints):**
- `GET /servers` — list all MCP servers with connection status
- `GET /servers/{name}/tools` — list tools for a server
- `GET /config` — config file path
- `GET /config/content` — full config contents
- `POST /config/save` — save config with validation + reload
- `GET /config/mcpServers` — just the mcpServers section
- `POST /config/mcpServers/save` — update mcpServers section + reload
- `GET /logs`, `GET /logs/categorized`, `GET /logs/sources` — log retrieval
- `POST /logs/clear/{category}`, `POST /logs/clear/all` — log management
- `POST /reload` — force config reload
- `POST /reinit/{server_name}` — tear down and reconnect a server
- `POST /install-dependencies` — pip install from requirements.txt
- `POST /servers/{name}/enable`, `/disable` — server enable/disable
- `POST /servers/{name}/tools/{tool}/enable`, `/disable` — tool enable/disable
- `GET /status` — server status summary
- `GET /stats` — uptime and version
- `GET /metrics` — execution metrics
- `GET /requirements/content`, `POST /requirements/save` — requirements.txt management
- `GET /aggregate_openapi` — unified OpenAPI spec across all servers

**Current state:**
- Has a comment at the top: imports from main.py are "to be phased out"
- Imports `load_config`, `reload_config_handler`, `_mount_or_remount_fastmcp`, `unmount_servers`, `create_sub_app`, `error_envelope` from `mcpo.main`
- All functionality duplicates what's inline in `main.py`

**What it replaces:**
- The ~800 lines of `/_meta` endpoint definitions inside `main.py:build_main_app()`
- Once wired, `main.py` can `include_router(meta_router, prefix="/_meta")` and remove all inline endpoint definitions

---

### 2.5 `api/routers/tools.py` — Dynamic Tool Endpoints Router (76 lines)

**Status**: Earlier WIP version, superseded by `main.py`  
**Intent**: Extract `create_dynamic_endpoints()` from `main.py` into a reusable router

**Current state:**
- Contains a `create_dynamic_endpoints(app, api_dependency)` function nearly identical to `main.py`'s version
- **Missing** `operation_id` with `server_key` prefix (which `main.py` added later for uniqueness)
- **Missing** the evolved error handling that `main.py` now has

**What it would replace:**
- `main.py:create_dynamic_endpoints()` — but the main.py version has evolved past this

**Action needed**: Update to match `main.py`'s current version, then swap imports.

---

### 2.6 `api/routers/health.py` — Health Endpoint (10 lines)

**Status**: Minimal stub, not mounted  
**Intent**: Standalone `/healthz` endpoint

**What it provides:**
```python
@router.get("/healthz")
async def healthz():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
```

**What it replaces:**
- `main.py:_register_health_endpoint()` — but the main.py version is more feature-rich (includes generation count, last reload time, per-server connection status)

**Action needed**: Either enrich to match `main.py`'s version or drop in favor of keeping the main.py implementation.

---

### 2.7 `api/routers/admin.py` — Admin Router (229 lines, PARTIALLY ACTIVE)

**Status**: Mixed — one function is actively imported, rest is WIP/dead

**Active code:**
- `_mount_or_remount_fastmcp(app, base_path)` at line 25 — **THE canonical definition**. Imported by `main.py` and `meta.py`. Handles spawning a FastMCP proxy sub-app under the main app, applying `MCPToolFilterMiddleware`, and managing lifespan.

**WIP/Dead code:**
- `unmount_servers()` at line 182 — duplicate of `main.py`'s version, zero external callers
- `POST /_meta/env` endpoint — writes arbitrary env vars to `.env` file AND `os.environ`. Active but has security implications (no auth check beyond global API key).

---

### 2.8 `app/factory.py` — App Factory Pattern (28 lines)

**Status**: Broken import  
**Intent**: Provide a clean `create_app()` factory function

**Current code:**
```python
from mcpo.main import build_app  # BUG: main.py defines build_main_app, not build_app
```

**What it would replace:**
- Direct calls to `main.py:build_main_app()` — allows cleaner testing and entry point configuration

**Fix needed**: Change `build_app` to `build_main_app`.

---

## 3. Active Services (Production Code)

### 3.1 `services/state.py` (180 lines) — ACTIVE

**Purpose**: Persistent state for server/tool/provider/model enable/disable and model favorites  
**Storage**: `mcpo_state.json` on disk  
**Thread safety**: `threading.RLock` on all read/write operations  
**Used by**: `main.py`, `proxy.py`, `meta.py`, `admin.py`, `mcp_tool_filter.py`, `mcp_tools.py`, `utils/main.py`

**Sections:**
- Server states: `set_server_enabled()`, `is_server_enabled()`, `get_all_states()`
- Tool states: `set_tool_enabled()`, `is_tool_enabled()`
- Provider states: `get_provider_state()`, `set_provider_enabled()`, `is_provider_enabled()`
- Model states: `is_model_enabled()`, `set_model_enabled()`
- Favorites: `add_favorite_model()`, `remove_favorite_model()`, `is_model_favorite()`, `get_favorite_models()`

### 3.2 `services/logging.py` (188 lines) — ACTIVE

**Purpose**: In-memory log buffer for UI display with categorization and source filtering  
**Used by**: `proxy.py`, `meta.py`, main.py (indirectly)

### 3.3 `services/logging_handlers.py` — ACTIVE

**Purpose**: `BufferedLogHandler` — Python logging handler that captures log records into a shared buffer for `LogManager`  
**Used by**: `main.py`, `proxy.py`

### 3.4 `services/metrics.py` (80 lines) — ACTIVE

**Purpose**: `MetricsAggregator` — tracks total calls, errors by error code (disabled, invalid_timeout, timeout, unexpected)  
**Used by**: `utils/main.py` (tool handlers), `meta.py` (metrics endpoint)

### 3.5 `services/runner.py` (212 lines) — ACTIVE

**Purpose**: `RunnerService` — centralized tool execution with timeout enforcement, metrics tracking, background task management  
**Used by**: `utils/main.py` (tool handlers), `chat.py` (tool execution loop)

### 3.6 `services/chat_sessions.py` (130 lines) — ACTIVE

**Purpose**: In-memory chat session management for the `/sessions` (chat.py) router  
**Provides**: `ChatSession`, `ChatStep`, `ChatSessionManager` (async lock, CRUD), global singleton via `get_chat_session_manager()`  
**Used by**: `chat.py`

### 3.7 `services/mcp_tools.py` (120 lines) — ACTIVE

**Purpose**: Collect MCP `ClientSession` objects from mounted sub-apps, respecting enabled/disabled states and optional allowlists  
**Provides**: `collect_enabled_mcp_sessions()`, `collect_enabled_mcp_sessions_with_names()`, `sanitize_tool_name()`  
**Used by**: `chat.py`

---

## 4. Active Utilities

### 4.1 `utils/main.py` (733 lines) — ACTIVE, Core

**Purpose**: The heart of MCP-to-OpenAPI translation. Contains:
- `process_tool_response()` — Convert MCP `CallToolResult` to JSON
- `_process_schema_property()` — Recursive JSON Schema → Pydantic type conversion (handles $ref, anyOf, oneOf, allOf, arrays, objects, enums, nullables)
- `get_model_fields()` — Schema → Pydantic field mapping
- `get_tool_handler()` — Build endpoint functions with timeout, enable/disable, metrics, error enveloping
- `create_error_envelope()`, `create_success_envelope()` — Response helpers
- `validate_tool_schema()`, `extract_tool_metadata()`, `safe_json_loads()` — Utility functions

### 4.2 `utils/config.py` (47 lines) — ACTIVE

**Purpose**: `${VAR}` placeholder interpolation in config env/headers values  
**Provides**: `interpolate_env_placeholders()`, `interpolate_env_placeholders_in_config()`  
**Used by**: `main.py:load_config()`  
**Note**: `proxy.py` has its own inline duplicate `_interpolate_env_placeholders()`.

### 4.3 `utils/config_watcher.py` (162 lines) — ACTIVE

**Purpose**: Watchdog-based config file watcher for hot reload  
**Provides**: `ConfigChangeHandler`, `ConfigWatcher` with debouncing and async callback  
**Used by**: `main.py:build_main_app()` when `--hot-reload` is enabled  
**To be replaced by**: `services/hot_reload.py` (more feature-complete)

### 4.4 `utils/auth.py` (121 lines) — ACTIVE

**Purpose**: Authentication utilities  
**Provides**:
- `get_verify_api_key()` — FastAPI dependency for Bearer token validation
- `APIKeyMiddleware` — ASGI middleware supporting both Bearer and Basic auth
- Commented-out JWT functions (`create_token`, `decode_token`) — WIP for future session auth

**Used by**: `main.py:build_main_app()` (API key middleware and dependency)  
**Note**: Imports `passlib`, `jwt` at module level but JWT functions are commented out. These imports are unused.

---

## 5. Middleware

### 5.1 `middleware/mcp_tool_filter.py` (207 lines) — ACTIVE

**Purpose**: ASGI middleware that intercepts MCP protocol messages to filter tools based on `StateManager`  
**Intercepts**:
- `tools/list` responses → removes disabled tools from the response
- `tools/call` requests → blocks calls to disabled tools with 403 JSON-RPC error  
**Supports**: Single-server mode (when `server_name` is set) and aggregate proxy mode (infers server from tool name pattern `server__tool`)  
**Used by**: `proxy.py` (on all FastMCP sub-apps) and `admin.py:_mount_or_remount_fastmcp()` (on main app's FastMCP mount)

---

## 6. Provider Layer — Current Wiring Status

### What should happen (target state):

```
chat.py:_get_client_for_model()
  ├─ minimax/* → MiniMaxClient (→ AnthropicClient at api.minimax.io/anthropic)  ✅
  ├─ google/* → GoogleClient (→ Gemini at generativelanguage.googleapis.com)    ✅
  ├─ anthropic/* → AnthropicClient (→ api.anthropic.com)                        ❌ NOT WIRED
  ├─ openai/* → OpenAIClient (→ api.openai.com)                                ❌ NOT WIRED
  ├─ glm/* → GLMClient (→ TBD)                                                 ❌ NOT BUILT
  └─ * → OpenRouterClient (fallback)                                            ✅

completions.py (stateless /v1/chat/completions)
  ├─ openai/* → providers/openai.py                                             ❌ Uses inline duplicate
  ├─ anthropic/* → providers/anthropic.py                                       ❌ Uses inline duplicate
  ├─ google/* → providers/google.py                                             ❌ Uses inline duplicate
  ├─ minimax/* → providers/minimax.py (via Anthropic)                           ⚠️  Partial (inline AnthropicProvider)
  ├─ glm/* → TBD                                                               ❌ NOT BUILT
  └─ * → OpenRouter (inline)                                                    ✅
```

### Completions.py inline provider duplication:

`completions.py` contains ~600 lines of inline provider classes that duplicate the `providers/` modules:
- `OpenAICompatibleProvider` — duplicates `providers/openai.py`
- `AnthropicProvider` — duplicates `providers/anthropic.py`
- `GeminiProvider` — duplicates `providers/google.py`
- `MiniMaxProvider` — delegates to inline `AnthropicProvider` (partially aligned)

The intent is clearly to eventually replace these inline classes with imports from `providers/`.

---

## 7. Env Var Inconsistencies

| Provider | `providers/*.py` uses | `completions.py` uses | Correct |
|----------|----------------------|----------------------|---------|
| OpenAI | `OPEN_AI_API_KEY` | `OPEN_AI_API_KEY` | Should also accept `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` | ✅ Consistent |
| Google | `GEMINI_API_KEY` first, then `GOOGLE_API_KEY` | `GEMINI_API_KEY` first, then `GOOGLE_API_KEY` | ✅ Consistent |
| MiniMax | `MINIMAX_API_KEY` | `MINIMAX_API_KEY` | ✅ Consistent |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` | ✅ Consistent |

---

## 8. What Replaces What — Transition Map

| Current (in main.py/inline) | Target (WIP module) | Status |
|-----------------------------|---------------------|--------|
| `main.py:load_config()` | `services/registry.py:ServerRegistry.load_config()` | Ready, not swapped |
| `main.py:validate_server_config()` | `models/config.py:ServerConfig` (Pydantic validator) | Ready, not swapped |
| `main.py:error_envelope()` | `models/responses.py:ErrorResponse` + `error_envelope()` | Ready, not swapped |
| `main.py` inline `/_meta/*` endpoints (~800 lines) | `api/routers/meta.py` (920 lines) | Ready, not swapped |
| `utils/config_watcher.py:ConfigWatcher` | `services/hot_reload.py:HotReloadService` | Ready, not swapped |
| `main.py:_register_health_endpoint()` | `api/routers/health.py` | Stub only, needs enrichment |
| `completions.py:OpenAICompatibleProvider` | `providers/openai.py:OpenAIClient` | Ready, not swapped |
| `completions.py:AnthropicProvider` | `providers/anthropic.py:AnthropicClient` | Ready, not swapped |
| `completions.py:GeminiProvider` | `providers/google.py:GoogleClient` | Ready, not swapped |
| `main.py:build_main_app()` entry | `app/factory.py:create_app()` | Broken import, needs fix |
| Ad-hoc dicts everywhere | `models/domain.py` typed models | Ready, not consumed |

---

## 9. Duplicate Code Inventory

| Code | Location A | Location B | Notes |
|------|-----------|-----------|-------|
| `_interpolate_env_placeholders()` | `utils/config.py` | `proxy.py:180` | Proxy has inline copy |
| `error_envelope()` | `main.py` | `utils/main.py` | Both define it, slightly different |
| `error_envelope()` | `main.py` | `models/responses.py` | Third definition |
| `unmount_servers()` | `main.py` | `api/routers/admin.py:182` | admin.py version is dead |
| `create_dynamic_endpoints()` | `main.py:407` | `api/routers/tools.py` | tools.py is earlier version |
| `/_meta/servers` endpoint | `main.py` (inline) | `api/routers/meta.py` | Both exist |
| OpenAI provider logic | `providers/openai.py` | `completions.py` (inline) | ~600 lines duplicated |
| Anthropic provider logic | `providers/anthropic.py` | `completions.py` (inline) | ~400 lines duplicated |
| Google provider logic | `providers/google.py` | `completions.py` (inline) | ~300 lines duplicated |

---

## 10. Summary of Remaining Work

### Must Fix (Bugs)
1. `app/factory.py` — `build_app` → `build_main_app` import fix
2. `utils/auth.py` — Remove unused `passlib`, `jwt` imports (or leave if JWT is imminent)
3. `OPEN_AI_API_KEY` — Add fallback to `OPENAI_API_KEY` in both `providers/openai.py` and `completions.py`

### Should Wire (Provider Routing)
4. `chat.py:_get_client_for_model()` — Add `AnthropicClient` and `OpenAIClient` routing
5. `completions.py` — Replace inline provider classes with imports from `providers/`

### Should Build (New Providers)
6. GLM/Zhipu provider — OpenAI-compatible at `https://open.bigmodel.cn/api/paas/v4/` (or Anthropic-compatible if endpoint exists)

### Can Swap When Ready (Refactoring)
7. Replace `main.py` inline `/_meta` endpoints with `include_router(meta_router)`
8. Replace `load_config()` / config CRUD with `ServerRegistry`
9. Replace `ConfigWatcher` with `HotReloadService`
10. Replace ad-hoc dicts with `models/` typed models
11. Consolidate `error_envelope()` to single source (`models/responses.py`)
12. Deduplicate `_interpolate_env_placeholders()` between `utils/config.py` and `proxy.py`
