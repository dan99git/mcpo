# ⚡️ mcpo

Expose any MCP tool as an OpenAPI-compatible HTTP server—instantly.

mcpo is a dead-simple proxy that takes an MCP server command and makes it accessible via standard RESTful OpenAPI, so your tools "just work" with LLM agents and apps expecting OpenAPI servers.

No custom protocol. No glue code. No hassle.

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

## 🚀 Quick Usage

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
    } // Streamable HTTP MCP Server
  }
}
```

Each tool will be accessible under its own unique route, e.g.:
- http://localhost:8000/memory
- http://localhost:8000/time

Each with a dedicated OpenAPI schema and proxy handler. Access full schema UI at: `http://localhost:8000/<tool>/docs`  (e.g. /memory/docs, /time/docs)

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

## � MCP Server Settings UI (`/mcp`)

When the optional React settings app is present, it mounts at `/mcp` (fallback minimal list UI also available at `/ui`). It exposes live server + tool management.

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

Disabled items return HTTP 403:

```json
{ "ok": false, "error": { "message": "Tool disabled", "code": "disabled" } }
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
