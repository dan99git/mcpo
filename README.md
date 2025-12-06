# 🌐 OpenHubUI (formerly MCPO)

**Unified MCP Gateway with Visual Management**

A microservice that aggregates any MCP server—stdio, SSE, or streamable-http—into a **single streamable-http endpoint** that OpenWebUI and other clients can consume directly.

---

## 🎯 What It Does

OpenHubUI solves a fundamental problem: **most MCP servers use stdio or SSE**, but many clients (including OpenWebUI) only support streamable-http for security reasons—stdio requires spawning local processes, and SSE has cross-origin limitations.

OpenHubUI bridges this gap:

| You configure | OpenHubUI exposes |
|---------------|-------------------|
| stdio servers (npx, uvx, python) | Single streamable-http endpoint |
| SSE servers (remote APIs) | Single streamable-http endpoint |
| Streamable-http servers | Single streamable-http endpoint |

**One endpoint. All your tools. Any protocol.**

### Why This Matters

- **Security**: Clients don't need to spawn processes or handle raw stdio
- **Compatibility**: Works with any MCP server regardless of transport
- **Simplicity**: Point OpenWebUI at one URL, get all tools
- **Control**: Enable/disable servers and individual tools via UI

---

## ✨ Key Features

### Visual Management UI
A web interface at `http://localhost:8000/ui` to:
- **Add/remove** MCP servers
- **Enable/disable** entire servers or individual tools
- **Monitor** connection status and logs
- **Configure** without editing JSON files

### Dual Protocol Output
- **Port 8000**: OpenAPI/REST endpoints + Admin UI
- **Port 8001**: Single aggregated streamable-http MCP endpoint

### Hot Reload
Change configuration without restarting—servers reconnect automatically.

---

## 📋 Table of Contents
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [OpenWebUI Integration](#openwebui-integration)
- [Development Roadmap](#development-roadmap)
- [Legacy Documentation](#legacy-mcpo-documentation)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      OpenWebUI / Clients                    │
└─────────────────────────┬───────────────────────────────────┘
                          │ streamable-http
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    OpenHubUI (Port 8001)                    │
│         Single aggregated MCP endpoint: /mcp                │
│         All tools from all servers in one place             │
└─────────────────────────┬───────────────────────────────────┘
                          │ stdio / SSE / streamable-http
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Your MCP Servers                         │
│  ├── npx @playwright/mcp (stdio)                            │
│  ├── uvx mcp-server-time (stdio)                            │
│  ├── https://api.example.com/mcp (SSE)                      │
│  └── Any other MCP server                                   │
└─────────────────────────────────────────────────────────────┘
```

### Port Summary

| Port | Protocol | Purpose |
|------|----------|--------|
| 8000 | HTTP/REST | **OpenAPI proxy** (`/{server}/{tool}`), Admin UI (`/ui`), management APIs (`/_meta/*`) |
| 8001 | Streamable HTTP (MCP) | **Aggregated MCP endpoint** (`/mcp`) for clients that speak MCP natively |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Node.js (for npx-based MCP servers)
- `uv` (recommended) or `pip`

### Installation

```bash
# Using uv (recommended)
uvx mcpo --config mcpo.json --hot-reload

# Using pip
pip install mcpo
mcpo --config mcpo.json --hot-reload
```

### Basic Configuration

Create `mcpo.json`:
```json
{
  "mcpServers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time"],
      "enabled": true
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"],
      "enabled": true
    }
  }
}
```

### Access Points

- **Admin UI**: http://localhost:8000/ui
- **API Docs**: http://localhost:8000/docs
- **Server Docs**: http://localhost:8000/{server-name}/docs
- **OpenWebUI Endpoint**: http://localhost:8001/{server-name}

---

## ⚙️ Configuration

### Server Configuration

```json
{
  "mcpServers": {
    "server-name": {
      // stdio server (local command)
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "env": {"CUSTOM_VAR": "value"},
      "enabled": true,
      
      // OR SSE server (remote)
      "type": "sse",
      "url": "http://example.com/sse",
      "headers": {"Authorization": "Bearer token"},
      "enabled": true,
      
      // OR Streamable HTTP server
      "type": "streamable-http",
      "url": "http://example.com/mcp",
      "enabled": true
    }
  }
}
```

### State Management

Tool states persist in `mcpo_state.json`:
```json
{
  "servers": {
    "playwright": {
      "enabled": true,
      "tools": {
        "browser_navigate": true,
        "browser_snapshot": false  // Individual tool disabled
      }
    }
  }
}
```

### Command Line Options

```bash
mcpo serve \
  --config mcpo.json \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key "your-secret-key" \
  --hot-reload \
  --cors-allow-origins "*"
```

---

## 🔗 OpenWebUI Integration

Open WebUI (v0.6.31+) natively supports MCP via **Streamable HTTP only**. Most MCP servers use stdio or SSE transports, which Open WebUI cannot consume directly. OpenHubUI bridges this gap by proxying any MCP server to Streamable HTTP.

### Why Use OpenHubUI Instead of Direct MCP?

- **Transport compatibility**: Converts stdio/SSE servers to Streamable HTTP
- **Better tool execution**: Native MCP works more reliably than OpenAPI tool integrations
- **Centralized management**: One place to configure all MCP servers
- **Aggregation**: All tools available through a single connection

### Connection Options

OpenHubUI exposes MCP servers in two ways on port 8001:

| Approach | URL | Use Case |
|----------|-----|----------|
| **Aggregated** | `http://localhost:8001` | All servers as one connection—simple setup |
| **Per-server** | `http://localhost:8001/{server-name}` | Each server gets its own toggle in Open WebUI |

### Setup Steps

1. **Start OpenHubUI** with your MCP servers configured in `mcpo.json`

2. **In Open WebUI**: Go to ⚙️ **Admin Settings** → **External Tools**

3. **Click + (Add Server)** and configure:
   - **Type**: MCP (Streamable HTTP)
   - **URL**: 
     - Docker: `http://host.docker.internal:8001`
     - Local: `http://localhost:8001`
     - Per-server: `http://host.docker.internal:8001/perplexity`
   - **Auth**: None (unless API key configured)
   - **Name**: e.g., "OpenHubUI" or the server name

4. **Save** and restart Open WebUI if prompted

### Example: Per-Server Setup

To give each MCP server its own toggle in Open WebUI:

```
# Add each server separately in Open WebUI External Tools:
http://host.docker.internal:8001/time        → Name: "Time"
http://host.docker.internal:8001/perplexity  → Name: "Perplexity"  
http://host.docker.internal:8001/playwright  → Name: "Playwright"
```

This lets you enable/disable specific servers directly in Open WebUI's interface.

---

## 🗺️ Development Roadmap

### ✅ Phase 1: Core MCP Proxy (Current)
- [x] Multi-server configuration
- [x] Hot-reload support
- [x] Admin UI
- [x] State persistence
- [x] Health monitoring
- [x] OpenAPI documentation

### 🔄 Phase 2: Audio Services (Experimental)
- [ ] Whisper speech-to-text
- [ ] Real-time transcription (WebSocket)
- [ ] Text-to-speech integration
- [ ] Audio streaming optimization

### 📅 Phase 3: AI Provider Aggregation
- [ ] Vercel AI SDK integration
- [ ] Unified `/v1/chat/completions` endpoint
- [ ] Multi-provider support (OpenAI, Claude, Gemini)
- [ ] Response format normalization

### 🔮 Phase 4: Sandboxed AI Agents
- [ ] Agent configuration framework
- [ ] Structured output enforcement
- [ ] Document-based RAG integration
- [ ] Agent-as-MCP-tool wrapper

[See ROADMAP.md for detailed timeline](./ROADMAP.md)

---

## 🛡️ Security

**⚠️ CRITICAL: Command Execution Risk**

OpenHubUI executes commands specified in your configuration. **Protect your config files:**

- Run with least privilege
- Validate all configuration sources
- Use strong API keys
- Never expose ports publicly without authentication
- Review `mcpo.json` before loading untrusted configs

---

## 📚 API Reference

### Management Endpoints

```bash
# List all servers and their status
GET /_meta/servers

# List tools for a specific server
GET /_meta/servers/{server-name}/tools

# Toggle server enable/disable
POST /_meta/servers/{server-name}/toggle

# Toggle individual tool
POST /_meta/servers/{server-name}/tools/{tool-name}/toggle

# Get metrics
GET /_meta/metrics
```

### Tool Endpoints

```bash
# Direct access (Port 8000)
POST /{server-name}/{tool-name}

# Streamable (Port 8001 - for OpenWebUI)
POST /{server-name}  # MCP protocol, not REST
```

---

## 🧪 Development

### Setup

```bash
git clone https://github.com/open-webui/mcpo.git
cd mcpo
uv sync --dev
```

### Run Tests

```bash
uv run pytest
```

### Local Development

```bash
# Run with local changes
uv run mcpo serve --config mcpo.json --hot-reload

# Run proxy separately
uv run python -m mcpo.proxy --config mcpo.json
```

---

## 🤝 Contributing

We welcome contributions! Whether it's:
- Bug fixes
- New features
- Documentation improvements
- Testing and feedback

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

---

## 📄 License

MIT License - see [LICENSE](./LICENSE)

---

## 🙏 Acknowledgments

Built on the [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic.

Designed as a companion for [OpenWebUI](https://github.com/open-webui/open-webui).

---

<details>
<summary>

## 📜 Legacy MCPO Documentation

</summary>

### Original Purpose

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

## ⚠️ **SECURITY WARNING: Command Execution Risk**

mcpo is designed to execute commands specified in its configuration (e.g., via `--server-command` or `mcpServers` in a config file) to launch MCP servers. **This feature carries a significant security risk if configuration files or command-line arguments are compromised.**

- **Malicious Command Injection:** An attacker gaining control over your `mcpo` configuration could inject arbitrary shell commands, leading to remote code execution on the system running `mcpo`.
- **Protect Your Configuration:** Always ensure that `mcpo` configuration files (`mcpo.json`, `mcpo_state.json`, etc.) and startup parameters are protected with appropriate file permissions and access controls.
- **Run with Least Privilege:** Run `mcpo` under a user account with the absolute minimum necessary permissions.
- **Validate Inputs:** When dynamically generating or accepting configuration for `mcpo` from untrusted sources, implement strict validation and sanitization to prevent malicious command injection.

**Always be aware of the source and integrity of your `mcpo` configurations.**

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

## 🔗 OpenWebUI Pipe Integration

mcpo provides first-class integration with OpenWebUI's Pipe system, allowing you to use Google GenAI models with native MCP tool access through OWUI's interface.

### Setup

1. **Install google-genai SDK:**
   ```bash
   pip install google-genai~=0.5.0
   ```

2. **Configure Environment:**
   ```bash
   export GOOGLE_API_KEY="your-google-api-key"  # For Developer API
   # OR for Vertex AI:
   export GOOGLE_GENAI_USE_VERTEXAI=true
   export GOOGLE_CLOUD_PROJECT="your-project"
   export GOOGLE_CLOUD_LOCATION="us-central1"
   ```

3. **Create Model Configuration:**
   Add to your models registry (via UI or API):
   ```json
   {
     "id": "gemini-1.5-flash",
     "name": "Gemini 1.5 Flash",
     "provider": "google-genai",
     "model": "gemini-1.5-flash",
     "adapter": "mcpo.providers.google_genai:GoogleGenAIAdapter",
     "credentials": {
       "api_key_env": "GOOGLE_API_KEY"
     },
     "parameters": {
       "temperature": 0.7,
       "max_tokens": 1000
     }
   }
   ```

### OWUI Pipe Function

Create a pipe function in your OWUI installation:

```python
from typing import AsyncGenerator
import aiohttp

async def pipe(
    body: dict,
    __event_emitter__=None,
    __event_call__=None,
    **kwargs
) -> AsyncGenerator[str, None]:
    """
    Pipe function for Google GenAI with MCP tools.
    """
    async with aiohttp.ClientSession() as session:
        # Send to mcpo's OWUI pipe endpoint
        async with session.post(
            "http://localhost:8000/_pipe/chat/stream",  # Adjust host/port as needed
            json={
                "model": body.get("model", "gemini-1.5-flash"),
                "messages": body.get("messages", []),
                "stream": True,
                "metadata": {
                    "temperature": body.get("temperature", 0.7),
                    "max_tokens": body.get("max_tokens", 1000),
                    "tool_servers": body.get("tool_servers", [])  # Optional server allowlist
                }
            }
        ) as response:
            async for line in response.content:
                yield line.decode('utf-8')
```

### Features

- **Native MCP Tool Calling:** Tools are invoked directly within the Google GenAI adapter
- **OpenAI-Compatible Streaming:** Returns SSE chunks compatible with OWUI's streaming pipeline
- **Server Allowlisting:** Control which MCP servers are available via `tool_servers` parameter
- **Error Handling:** Structured error responses with retry logic
- **Safety Settings:** Configurable content safety and response schemas

### Environment Variables

- `MCPO_OWUI_PIPE_ENABLED=true` (default: true) - Enable/disable OWUI pipe endpoint
- `MCPO_OPENAI_COMPAT_ENABLED=false` (default: false) - Enable OpenAI-compatible /v1/chat/completions (deferred)

### API Endpoints

- `POST /_pipe/chat/stream` - OWUI pipe endpoint accepting form data
- `POST /_models/chat/stream` - Internal streaming endpoint
- `POST /_models/chat/completions` - Internal non-streaming endpoint

### Sample Request

```bash
curl -X POST "http://localhost:8000/_pipe/chat/stream" \
  -F "model=gemini-1.5-flash" \
  -F "messages=[{\"role\":\"user\",\"content\":\"Hello\"}]" \
  -F "stream=true" \
  -F "metadata={\"temperature\":0.7,\"tool_servers\":[\"time\",\"memory\"]}"
```

### Response Format

Returns Server-Sent Events (SSE) with OpenAI-compatible chunks:

```
data: {"id": "chatcmpl-123", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "Hello"}}]}

data: [DONE]
```

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

## 🔧 Requirements

- Python 3.8+
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

**Made with ❤️ by the OpenWebUI community**
# ������ mcpo

Expose any MCP tool as an OpenAPI-compatible HTTP server���instantly.

mcpo is a dead-simple proxy that takes an MCP server command and makes it accessible via standard RESTful OpenAPI, so your tools "just work" with LLM agents and apps expecting OpenAPI servers.

No custom protocol. No glue code. No hassle.

### ���� Release Highlights (0.0.18)
> Snapshot of what���s new in this development baseline versus earlier public releases.

- ������� **Management UI (`/ui`)**: Live logs, config + requirements editing, tool visibility & toggling.
- ���� **Internal Self-Managing Tools**: `mcpo_internal` exposes management operations at `/mcpo/openapi.json`.
- ��+��� **Dynamic Config & Dependencies**: Edit `mcpo.json` & `requirements.txt` (with backups) and hot-reload without restarts.
- �Ŧ **Per-Tool Timeouts**: Default + per-request override + max guard; consistent error envelope.
- ���� **Structured Output (Experimental)**: `--structured-output` adds a normalized collection envelope.
- ���� **SSE & Streamable HTTP Support**: Specify `--server-type sse|streamable-http`; protocol-version header injected automatically.
- ���� **Enable/Disable Controls**: Servers & individual tools manageable via meta endpoints and UI.
- ���� **Rich Meta API (`/_meta/*`)**: Introspection, config content, logs, reload, reinit, dependency save endpoints.

Upgrade notes:
- No breaking CLI changes; existing single-server usage unchanged.
- To enable internal management tools, add the `mcpo_internal` server block in your config (see config example below).
- Structured output is optional & evolving���omit the flag for prior simple `{ ok, result }` responses.

_For full details, see sections: Management Interface & Internal Tools, Tool Timeout, Structured Output._

### ���ᴩ� Development Status
**System Status**: ԣ� Fully operational and tested  
**Test Suite**: 49/49 tests passing  
**Recovery**: All functionality restored and verified working  
**Last Verified**: August 2025 - Complete system recovery with all MCP servers operational

## ���� Why Use mcpo Instead of Native MCP?

MCP servers usually speak over raw stdio, which is:

- ���� Inherently insecure
- ��� Incompatible with most tools
- ���� Missing standard features like docs, auth, error handling, etc.

mcpo solves all of that���without extra effort:

- ԣ� Works instantly with OpenAPI tools, SDKs, and UIs
- ���� Adds security, stability, and scalability using trusted web standards
- ���� Auto-generates interactive docs for every tool, no config needed
- ���� Uses pure HTTP���no sockets, no glue code, no surprises

What feels like "one more step" is really fewer steps with better outcomes.

mcpo makes your AI tools usable, secure, and interoperable���right now, with zero hassle.

## �+� Fork Transparency & Scope

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

## �+����� Quick Usage

We recommend using uv for lightning-fast startup and zero config.

```bash
uvx mcpo --port 8000 --api-key "top-secret" -- your_mcp_server_command
```

Or, if you���re using Python:

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

That���s it. Your MCP tool is now available at http://localhost:8000 with a generated OpenAPI schema ��� test it live at [http://localhost:8000/docs](http://localhost:8000/docs).

���� **To integrate with Open WebUI after launching the server, check our [docs](https://docs.openwebui.com/openapi-servers/open-webui/).**

### ���� Using a Config File

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

### ���� Automatic Python Dependency Management

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

## ���� MCP Server Settings UI (`/mcp`)

mcpo provides multiple interfaces for monitoring and managing your MCP servers:

### ������� Management UI (`/ui`)

A comprehensive web interface for real-time server management:

- **Live Server Monitoring**: View all configured servers with connection status and tool counts
- **Real-time Logs**: Monitor server activity with live log streaming (last 500 entries)
- **Configuration Editor**: Edit `mcpo.json` directly with JSON validation and instant reload
- **Dependency Management**: Manage Python dependencies via `requirements.txt` with automatic installation
- **Tool Visibility**: Expand server panels to see all available tools and their enable/disable status

Access the management interface at `http://localhost:8000/ui` when running mcpo.

### ���ᴩ� Internal MCP Tools (`/mcpo`)

mcpo exposes its own management capabilities as discoverable MCP tools, making it a self-managing system. These tools are available at `http://localhost:8000/mcpo/openapi.json`:

- **`install_python_package`**: Dynamically install Python packages via pip
- **`get_config`**: Retrieve the current `mcpo.json` configuration
- **`post_config`**: Update the configuration and trigger server reload
- **`get_logs`**: Retrieve the last 20 server log entries

This allows AI models and external systems to manage mcpo programmatically, enabling self-modifying and adaptive tool ecosystems.

### ���� Legacy Settings UI (`/mcp`)

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
1. Main View: Server List ��� lists all configured servers; theme switch; dynamic >40 tools warning.
2. Server Item ��� master toggle; semi-transparent when disabled; expandable if tools exist; rotating chevron; status label (Disabled / No tools or prompts / X tools enabled). Tool tags toggle individual enabled state with distinct styling.
3. Add New Server ��� "New MCP Server" button opens modal with two tabs:
  - Install from Git: analyze repo ��� show Name, Start Command, Dependencies, required secret inputs ��� Install Server.
  - Manual Setup: enter Name (auto initial), Start Command, Dependencies, dynamic ENV variable rows (+ Add Variable / remove) ��� Add Server (disabled until Name present).
  - Modal closable via X, Escape, or backdrop.

> Added/removed servers persist only in config mode (writes to the active config JSON then invokes reload).

## ���� LLM / Chat Model Automation

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
- Ad���hoc package install (internal MCP tool): `install_python_package`

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
  - Via internal tool: call `get_config` ��� returns JSON (`mcpo.json`).
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
1. Call `/_meta/servers` ��� inventory.
2. Fetch & parse config (tool or HTTP) ��� check name uniqueness; insert `analytics` block.
3. Save config (tool or HTTP) ��� server loads.
4. Poll `/_meta/servers` until `analytics.connected==true` or timeout.
5. For each server: `GET /_meta/servers/{server}/tools`; where tool.name==`debug` ��� POST disable endpoint.
6. Return success summary.

### Security & Governance Considerations

- Per-tool disable allows a supervising model or human reviewer to quarantine newly surfaced or high-risk tools without removing the server entirely.
- In-memory tool toggles reset on full restart (intentional) ensuring the file-backed config stays minimal; persistent tool-level governance can be layered externally (planned optional persistence hook).
- Config save endpoints always create a timestamped backup before mutation, enabling recovery if an LLM introduces invalid structure.
- Validation rejects malformed JSON early; partial merges are not applied���changes are atomic.

### Envelope & Observability for Agents

- Uniform success envelope `{ "ok": true, ... }`; failures: `{ "ok": false, "error": { code, message, data? } }` simplifies tool calling logic.
- Timeouts yield code `timeout`; disabled yields code `disabled`; invalid changes code `invalid_config` (or similar) enabling structured branching without natural language parsing.
- Agents can monitor drift or reload events by comparing `generation` and `lastReload` fields from `GET /healthz`.

### Granular Tool Governance (Why It Matters)

Turning off a single expensive or risky tool prevents resource exhaustion or data exfiltration while keeping the rest of the server productive. This is crucial when exposing large multi-tool MCP servers to autonomous agents that may explore capabilities unpredictably. The UI surfaces these toggles prominently; the API mirrors them for automation.

---


## �+����� Requirements

- Python 3.11+ (runtime requirement; earlier README line corrected for consistency with pyproject)
- uv (optional, but highly recommended for performance + packaging)

## ���ᴩ� Development & Testing

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


## ���� License

MIT

## ���� Contributing

We welcome and strongly encourage contributions from the community!

Whether you're fixing a bug, adding features, improving documentation, or just sharing ideas���your input is incredibly valuable and helps make mcpo better for everyone.

Getting started is easy:

- Fork the repo
- Create a new branch
- Make your changes
- Open a pull request

Not sure where to start? Feel free to open an issue or ask a question���we���re happy to help you find a good first task.

## ԣ� Star History

<a href="https://star-history.com/#open-webui/mcpo&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/mcpo&type=Date" />
  </picture>
</a>

---

ԣ� Let's build the future of interoperable AI tooling together!

---

### ���� Security Model Summary
- Assumes trusted local network / single-tenant context
- Single shared API key (Bearer or Basic password) protects endpoints when provided
- `--strict-auth` also protects documentation UIs
- Use reverse proxies for TLS termination if not using native `--ssl-*` flags
- Prefer `--read-only` in packaged distributions to prevent unexpected mutation

### ���� Architectural Decisions
See `docs/DECISIONS.md` for detailed rationale, invariants, and the execution queue.

### ���� Production Deployment
For production setup instructions, see `docs/PRODUCTION_SETUP.md`.

</details>
