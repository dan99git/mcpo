# ⚡️ mcpo

Expose any MCP tool as an OpenAPI-compatible HTTP server—instantly.

mcpo is a dead-simple proxy that takes an MCP server command and makes it accessible via standard RESTful OpenAPI, so your tools "just work" with LLM agents and apps expecting OpenAPI servers.

No custom protocol. No glue code. No hassle.

### 🔔 Release Highlights (0.0.18)
> Snapshot of what’s new in this development baseline versus earlier public releases.

- 🎛️ **Management UI (`/ui`)**: Live logs, config + requirements editing, tool visibility & toggling.
- 🛠 **Internal Self-Managing Tools**: `mcpo_internal` exposes management operations at `/mcpo/openapi.json`.
- ♻️ **Dynamic Config & Dependencies**: Edit `mcpo.json` & `requirements.txt` (with backups) and hot-reload without restarts.
- ⏱ **Per-Tool Timeouts**: Default + per-request override + max guard; consistent error envelope.
- 🧪 **Structured Output (Experimental)**: `--structured-output` adds a normalized collection envelope.
- 🌐 **SSE & Streamable HTTP Support**: Specify `--server-type sse|streamable-http`; protocol-version header injected automatically.
- 🔐 **Enable/Disable Controls**: Servers & individual tools manageable via meta endpoints and UI.
- 🔎 **Rich Meta API (`/_meta/*`)**: Introspection, config content, logs, reload, reinit, dependency save endpoints.

Upgrade notes:
- No breaking CLI changes; existing single-server usage unchanged.
- To enable internal management tools, add the `mcpo_internal` server block in your config (see config example below).
- Structured output is optional & evolving—omit the flag for prior simple `{ ok, result }` responses.

_For full details, see sections: Management Interface & Internal Tools, Tool Timeout, Structured Output._

### 🛠️ Development Status
**System Status**: ✅ Fully operational and tested  
**Test Suite**: 49/49 tests passing  
**Recovery**: All functionality restored and verified working  
**Last Verified**: August 2025 - Complete system recovery with all MCP servers operational

## 🤔 Why Use mcpo Instead of Native MCP?

MCP servers usually speak over raw stdio, which is:

- 🔓 Inherently insecure
- ❌ Incompatible with most tools
- 🧩 Missing standard features like docs, auth, error handling, etc.

mcpo solves all of that—without extra effort:

- ✅ Works instantly with OpenAPI tools, SDKs, and UIs
- 🛡 Adds security, stability, and scalability using trusted web standards
- 🧠 Auto-generates interactive docs for every tool, no config needed
- 🔌 Uses pure HTTP—no sockets, no glue code, no surprises

What feels like "one more step" is really fewer steps with better outcomes.

mcpo makes your AI tools usable, secure, and interoperable—right now, with zero hassle.

## � Fork Transparency & Scope

This branch (`feat/phase1-baseline`) focuses on a minimal, locally-trusted deployment profile. Some auth-related libraries (JWT / passlib) have been removed for now to reduce surface area; only a simple shared API key model is supported. See `docs/DECISIONS.md` for architectural rationales and pending items.

### Current Non-Goals (Phase 1)
- Multi-tenant or per-user auth
- Fine-grained role-based access control
- Persistent metrics / observability backend
- Full state resumption (only enable/disable flags persist)

### Known Gaps
- No metrics endpoint yet (planned simple `/ _meta/metrics` returning counters)
- Structured output format is experimental and may change
- Management mutation operations are not yet exhaustively tested beyond happy paths
- SSE / streamable sessions do not auto-reconnect with backoff (manual reinit required)

If you need any of the above, treat this build as a baseline and extend accordingly.

## �🚀 Quick Usage

We recommend using uv for lightning-fast startup and zero config.

```bash
uvx mcpo --port 8000 --api-key "top-secret" -- your_mcp_server_command
```

Or, if you’re using Python:

```bash
pip install mcpo
mcpo --port 8000 --api-key "top-secret" -- your_mcp_server_command
```

To use an SSE-compatible MCP server, simply specify the server type and endpoint:

```bash
mcpo --port 8000 --api-key "top-secret" --server-type "sse" -- http://127.0.0.1:8001/sse
```

You can also provide headers for the SSE connection:

```bash
mcpo --port 8000 --api-key "top-secret" --server-type "sse" --header '{"Authorization": "Bearer token", "X-Custom-Header": "value"}' -- http://127.0.0.1:8001/sse
```

To use a Streamable HTTP-compatible MCP server, specify the server type and endpoint:

```bash
mcpo --port 8000 --api-key "top-secret" --server-type "streamable-http" -- http://127.0.0.1:8002/mcp
```

You can also run mcpo via Docker with no installation:

```bash
docker run -p 8000:8000 ghcr.io/open-webui/mcpo:main --api-key "top-secret" -- your_mcp_server_command
```

Example:

```bash
uvx mcpo --port 8000 --api-key "top-secret" -- uvx mcp-server-time --local-timezone=America/New_York
```

That’s it. Your MCP tool is now available at http://localhost:8000 with a generated OpenAPI schema — test it live at [http://localhost:8000/docs](http://localhost:8000/docs).

🤝 **To integrate with Open WebUI after launching the server, check our [docs](https://docs.openwebui.com/openapi-servers/open-webui/).**

### 🔄 Using a Config File

You can serve multiple MCP tools via a single config file that follows the [Claude Desktop](https://modelcontextprotocol.io/quickstart/user) format.

Enable hot-reload mode with `--hot-reload` to automatically watch your config file for changes and reload servers without downtime:

Start via:

```bash
mcpo --config /path/to/config.json
```

Or with hot-reload enabled:

```bash
mcpo --config /path/to/config.json --hot-reload
```

Example config.json:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"]
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=America/New_York"]
    },
    "mcp_sse": {
      "type": "sse", // Explicitly define type
      "url": "http://127.0.0.1:8001/sse",
      "headers": {
        "Authorization": "Bearer token",
        "X-Custom-Header": "value"
      }
    },
    "mcp_streamable_http": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8002/mcp"
    }, // Streamable HTTP MCP Server
    "mcpo_internal": {
      "type": "mcpo-internal",
      "enabled": true
    } // Internal MCPO management tools
  }
}
```

Each tool will be accessible under its own unique route, e.g.:
- http://localhost:8000/memory
- http://localhost:8000/time

Each with a dedicated OpenAPI schema and proxy handler. Access full schema UI at: `http://localhost:8000/<tool>/docs`  (e.g. /memory/docs, /time/docs)

### 📦 Automatic Python Dependency Management

mcpo automatically handles Python dependencies for smooth operation:

- **Startup Installation**: If a `requirements.txt` file exists in the working directory, mcpo will automatically run `pip install -r requirements.txt` during server startup
- **UI Management**: Edit dependencies directly through the management interface at `/ui` under the "Config" tab
- **Hot Reload**: Saving `requirements.txt` via the UI triggers automatic installation and server reload
- **Backup Protection**: Configuration and dependency files are automatically backed up before modification

This ensures all required packages are available without manual intervention, enabling self-contained deployments and dynamic dependency management.

### Health Endpoint

The server exposes `GET /healthz` providing a JSON snapshot:

```json
{
  "status": "ok",
  "generation": 1,
  "lastReload": "2025-08-17T20:15:00Z",
  "servers": {
    "time": {"connected": true, "type": "stdio"}
  }
}
```

### Tool Timeout

Use `--tool-timeout <seconds>` (default 30) to bound individual tool calls. Exceeded calls return HTTP 504 with a unified envelope:

```json
{ "ok": false, "error": { "message": "Tool timed out", "code": "timeout", "data": { "timeoutSeconds": 30 } } }
```

You can override per request:

- HTTP Header: `X-Tool-Timeout: 120`
- Query parameter: `?timeout=120`

Hard upper bound: configure `--tool-timeout-max` (default 600). Overrides beyond this are rejected with HTTP 400 (`code: invalid_timeout`). Invalid or non-positive values are also rejected. Structured output mode still includes an empty `output` collection for errors.

### Structured Output (Experimental)

Enable `--structured-output` to wrap each tool response in a normalized envelope:

```json
{
  "ok": true,
  "result": { /* original value (compat) */ },
  "output": {
    "type": "collection",
    "items": [
      { "type": "text|scalar|list|object|null", "value": ... }
    ]
  }
}
```

This early experimental format will evolve to support multiple mixed content items (text, images, resources) aligned with upcoming MCP spec extensions. Omit the flag to preserve the original simpler `{ ok, result }` shape.

### Read-Only Mode

For embedding mcpo in environments where configuration must not change at runtime, launch with:

```bash
mcpo --read-only --config ./mcpo.json
```

All mutating endpoints (reload, reinit, enable/disable, add/remove, config/requirements writes) will return HTTP 403 (`code: read_only`).

## 🖥 MCP Server Settings UI (`/mcp`)

mcpo provides multiple interfaces for monitoring and managing your MCP servers:

### 🎛️ Management UI (`/ui`)

A comprehensive web interface for real-time server management:

- **Live Server Monitoring**: View all configured servers with connection status and tool counts
- **Real-time Logs**: Monitor server activity with live log streaming (last 500 entries)
- **Configuration Editor**: Edit `mcpo.json` directly with JSON validation and instant reload
- **Dependency Management**: Manage Python dependencies via `requirements.txt` with automatic installation
- **Tool Visibility**: Expand server panels to see all available tools and their enable/disable status

Access the management interface at `http://localhost:8000/ui` when running mcpo.

### 🛠️ Internal MCP Tools (`/mcpo`)

mcpo exposes its own management capabilities as discoverable MCP tools, making it a self-managing system. These tools are available at `http://localhost:8000/mcpo/openapi.json`:

- **`install_python_package`**: Dynamically install Python packages via pip
- **`get_config`**: Retrieve the current `mcpo.json` configuration
- **`post_config`**: Update the configuration and trigger server reload
- **`get_logs`**: Retrieve the last 20 server log entries

This allows AI models and external systems to manage mcpo programmatically, enabling self-modifying and adaptive tool ecosystems.

### 🔄 Legacy Settings UI (`/mcp`)

When the optional React settings app is present, it mounts at `/mcp`. This provides the original management interface with theme toggle and server management capabilities.

### Meta / Control Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/_meta/servers` | GET | List servers (name, type, connected, enabled, basePath) |
| `/_meta/servers/{server}/tools` | GET | Tools for a server (name, enabled) |
| `/_meta/servers` | POST | Add server (config mode only) `{name, command?, url?, type?, env?}` |
| `/_meta/servers/{server}` | DELETE | Remove server (config mode only) |
| `/_meta/servers/{server}/enable` | POST | Enable server |
| `/_meta/servers/{server}/disable` | POST | Disable server |
| `/_meta/servers/{s}/tools/{t}/enable` | POST | Enable tool |
| `/_meta/servers/{s}/tools/{t}/disable` | POST | Disable tool |
| `/_meta/reload` | POST | Diff & reload config (add/remove/update servers) |
| `/_meta/reinit/{server}` | POST | Reinitialize single server session (fresh MCP handshake) |
| `/_meta/config` | GET | Return config path (if config mode) |
| `/_meta/logs` | GET | Return last 500 server log entries for UI display |
| `/_meta/config/content` | GET | Get formatted `mcpo.json` configuration content |
| `/_meta/config/save` | POST | Save and validate configuration with backup and reload |
| `/_meta/requirements/content` | GET | Get `requirements.txt` content |
| `/_meta/requirements/save` | POST | Save Python dependencies and trigger installation |

Disabled items return HTTP 403:

```json
{ "ok": false, "error": { "message": "Tool disabled", "code": "disabled" } }
```

### Management Behavior & Persistence

- Server add/remove is config-driven. The UI exposes an "Open Configuration" action to open your `mcpo.json`; use `/_meta/reload` (and optionally `/_meta/reinit/{server}`) after edits. No in-UI destructive modal is required.
- Server enable/disable toggles persist to `mcpo.json` under `mcpServers.<name>.enabled` and are seeded on startup/reload.
- Tool enable/disable toggles are enforced at call time and currently in-memory only (non-persistent) to keep config stable. A sidecar persistence may be added later if needed.

### Protocol Compliance Notes

- For remote MCP servers (SSE and Streamable HTTP), the `MCP-Protocol-Version` header is automatically injected (and merged with any provided headers) to comply with the 2025-06-18 spec.
- For single-server mode, the same header is applied based on CLI `--type` and `--header` parameters.

### cURL Examples

List servers:

```bash
curl -s http://localhost:8000/_meta/servers | jq
```

Enable/disable a server (persists to config in config mode):

```bash
curl -s -X POST http://localhost:8000/_meta/servers/time/disable | jq
curl -s -X POST http://localhost:8000/_meta/servers/time/enable | jq
```

Reload config after editing `mcpo.json`:

```bash
curl -s -X POST http://localhost:8000/_meta/reload | jq
```

Reinitialize a single server (reconnect + refresh tools):

```bash
curl -s -X POST http://localhost:8000/_meta/reinit/time | jq
```

Add/remove server (config mode only):

```bash
curl -s -X POST http://localhost:8000/_meta/servers \
  -H 'Content-Type: application/json' \
  -d '{"name":"time","command":"uvx","args":["mcp-server-time","--local-timezone=America/New_York"]}' | jq

curl -s -X DELETE http://localhost:8000/_meta/servers/time | jq
```

### Functional Brief (Summary)
- Light/dark theme toggle (Sun/Moon) persists per session.
- Global warning when total enabled tools across enabled servers > 40.
- Server panel: enable/disable switch, expandable tool list, live status (disabled / no tools / X tools enabled).
- Tool tags: individual enable/disable state.
- Add server modal: Git analysis path & manual setup path; environment variable rows with add/remove.
- Per-server restart (reinit) and global reload.

### Full Functional Brief

Functional Brief: MCP Server Settings UI
1. Main View: Server List – lists all configured servers; theme switch; dynamic >40 tools warning.
2. Server Item – master toggle; semi-transparent when disabled; expandable if tools exist; rotating chevron; status label (Disabled / No tools or prompts / X tools enabled). Tool tags toggle individual enabled state with distinct styling.
3. Add New Server – "New MCP Server" button opens modal with two tabs:
  - Install from Git: analyze repo → show Name, Start Command, Dependencies, required secret inputs → Install Server.
  - Manual Setup: enter Name (auto initial), Start Command, Dependencies, dynamic ENV variable rows (+ Add Variable / remove) → Add Server (disabled until Name present).
  - Modal closable via X, Escape, or backdrop.

> Added/removed servers persist only in config mode (writes to the active config JSON then invokes reload).

## 🤖 LLM / Chat Model Automation

mcpo has been ugraded so an LLM (via an agent framework) can safely introspect, plan, and apply configuration changes using only stable HTTP endpoints or the internal MCP management tools. This lets mcpo become a self-expanding tool substrate.

### Core Capabilities (Bulleted Reference)

Observability & Discovery:
- View servers & tool counts: UI dashboard OR `GET /_meta/servers` (JSON)
- List tools for a server (with enabled state): expand in UI OR `GET /_meta/servers/{server}/tools`
- Health snapshot & generation counter: `GET /healthz`

Server Lifecycle:
- Enable / disable a server: UI toggle OR `POST /_meta/servers/{server}/enable` / `.../disable` (persists to config)
- Reinitialize (fresh handshake) a single server: `POST /_meta/reinit/{server}`
- Add a server (config mode): `POST /_meta/servers` with JSON body (writes to config + reload)
- Remove a server (config mode): `DELETE /_meta/servers/{server}`
- Reload all servers after config changes: `POST /_meta/reload`

Tool Governance:
- Enable / disable individual tools: UI tag toggle OR `POST /_meta/servers/{s}/tools/{t}/enable|disable`
- Disabled tools return uniform `{ ok:false, error:{ code:"disabled" } }`
- Tool toggles are in-memory (non-persistent) by design today (config stays clean)

Configuration Management:
- Fetch config path: `GET /_meta/config`
- Read formatted config content: `GET /_meta/config/content`
- Save updated config (validated, backed up, then atomic apply): `POST /_meta/config/save`
- Internal MCP tools mirror this via `get_config` and `post_config`

Dependency Management:
- Read `requirements.txt`: `GET /_meta/requirements/content`
- Save & auto-install dependencies: `POST /_meta/requirements/save` (triggers pip + reload)
- Ad‑hoc package install (internal MCP tool): `install_python_package`

Logs & Monitoring:
- Live streaming log view in UI (rolling buffer ~500 entries)
- Programmatic recent logs: `GET /_meta/logs` or internal `get_logs` (last slice)

Protocol Support & Headers:
- SSE & Streamable HTTP MCP servers auto-injected `MCP-Protocol-Version`

Resilience & Safety:
- Atomic config writes with timestamped backups before mutation
- Uniform envelope for errors (`timeout`, `disabled`, `invalid_config`, etc.)
- Health `generation` increments on reload for drift detection

### How a Chat Model Can Add a New Server

1. Retrieve current configuration:
  - Via internal tool: call `get_config` → returns JSON (`mcpo.json`).
  - Or HTTP: `GET /_meta/config/content` (string) then parse JSON.
2. Parse & decide insertion (e.g., add a new SSE server named `issues`).
3. Create a modified config object adding:
```json
"issues": {
  "type": "sse",
  "url": "https://internal.example.com/mcp/sse",
  "headers": {"Authorization": "Bearer {{secret}}"},
  "enabled": true
}
```
4. Submit change:
  - Internal tool: call `post_config` with full updated config JSON (mcpo validates + backups + reloads).
  - Or HTTP: POST `/_meta/config/save` with body `{ "content": "<stringified formatted JSON>" }`.
5. Confirm:
  - `GET /_meta/servers` then locate `issues` with `connected:true` (after handshake) OR reinit if needed: `POST /_meta/reinit/issues`.

### Natural Language Orchestration Example

Prompt (user to agent):
> "Add a new SSE MCP server pointing to https://tools.example.com/mcp/sse with name analytics, then disable any tool named debug across all servers."

Agent plan (conceptual):
1. Call `/_meta/servers` → inventory.
2. Fetch & parse config (tool or HTTP) → check name uniqueness; insert `analytics` block.
3. Save config (tool or HTTP) → server loads.
4. Poll `/_meta/servers` until `analytics.connected==true` or timeout.
5. For each server: `GET /_meta/servers/{server}/tools`; where tool.name==`debug` → POST disable endpoint.
6. Return success summary.

### Security & Governance Considerations

- Per-tool disable allows a supervising model or human reviewer to quarantine newly surfaced or high-risk tools without removing the server entirely.
- In-memory tool toggles reset on full restart (intentional) ensuring the file-backed config stays minimal; persistent tool-level governance can be layered externally (planned optional persistence hook).
- Config save endpoints always create a timestamped backup before mutation, enabling recovery if an LLM introduces invalid structure.
- Validation rejects malformed JSON early; partial merges are not applied—changes are atomic.

### Envelope & Observability for Agents

- Uniform success envelope `{ "ok": true, ... }`; failures: `{ "ok": false, "error": { code, message, data? } }` simplifies tool calling logic.
- Timeouts yield code `timeout`; disabled yields code `disabled`; invalid changes code `invalid_config` (or similar) enabling structured branching without natural language parsing.
- Agents can monitor drift or reload events by comparing `generation` and `lastReload` fields from `GET /healthz`.

### Granular Tool Governance (Why It Matters)

Turning off a single expensive or risky tool prevents resource exhaustion or data exfiltration while keeping the rest of the server productive. This is crucial when exposing large multi-tool MCP servers to autonomous agents that may explore capabilities unpredictably. The UI surfaces these toggles prominently; the API mirrors them for automation.

---


## �🔧 Requirements

- Python 3.11+ (runtime requirement; earlier README line corrected for consistency with pyproject)
- uv (optional, but highly recommended for performance + packaging)

## 🛠️ Development & Testing

To contribute or run tests locally:

1.  **Set up the environment:**
    ```bash
    # Clone the repository
    git clone https://github.com/open-webui/mcpo.git
    cd mcpo

    # Install dependencies (including dev dependencies)
    uv sync --dev
    ```

2.  **Run tests:**
    ```bash
    uv run pytest
    ```

3.  **Running Locally with Active Changes:**

    To run `mcpo` with your local modifications from a specific branch (e.g., `my-feature-branch`):

    ```bash
    # Ensure you are on your development branch
    git checkout my-feature-branch

    # Make your code changes in the src/mcpo directory or elsewhere

    # Run mcpo using uv, which will use your local, modified code
    # This command starts mcpo on port 8000 and proxies your_mcp_server_command
    uv run mcpo --port 8000 -- your_mcp_server_command

    # Example with a test MCP server (like mcp-server-time):
    # uv run mcpo --port 8000 -- uvx mcp-server-time --local-timezone=America/New_York
    ```
    This allows you to test your changes interactively before committing or creating a pull request. Access your locally running `mcpo` instance at `http://localhost:8000` and the auto-generated docs at `http://localhost:8000/docs`.


## 🪪 License

MIT

## 🤝 Contributing

We welcome and strongly encourage contributions from the community!

Whether you're fixing a bug, adding features, improving documentation, or just sharing ideas—your input is incredibly valuable and helps make mcpo better for everyone.

Getting started is easy:

- Fork the repo
- Create a new branch
- Make your changes
- Open a pull request

Not sure where to start? Feel free to open an issue or ask a question—we’re happy to help you find a good first task.

## ✨ Star History

<a href="https://star-history.com/#open-webui/mcpo&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
  </picture>
</a>

---

✨ Let's build the future of interoperable AI tooling together!

---

### 🔐 Security Model Summary
- Assumes trusted local network / single-tenant context
- Single shared API key (Bearer or Basic password) protects endpoints when provided
- `--strict-auth` also protects documentation UIs
- Use reverse proxies for TLS termination if not using native `--ssl-*` flags
- Prefer `--read-only` in packaged distributions to prevent unexpected mutation

### 🧭 Architectural Decisions
See `docs/DECISIONS.md` for detailed rationale, invariants, and the execution queue.

### 🚀 Production Deployment
For production setup instructions, see `docs/PRODUCTION_SETUP.md`.
