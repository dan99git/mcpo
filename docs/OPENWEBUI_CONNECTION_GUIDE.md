# OpenWebUI Connection Guide

## ✅ Server Status: WORKING

Both servers are running:
- **Port 8000**: Admin UI & OpenAPI endpoints (requires Bearer auth if configured)
- **Port 8001**: Streamable HTTP MCP Proxy (NO AUTH by default)

## ✨ New: Unified Chat Completions Endpoint (Port 8000)
- Path: `/v1/chat/completions` (also `/v1/completions`)
- Shape: OpenAI-compatible request/response, supports `stream: true`
- Providers: auto-detected from model name, or set `provider` + `base_url` + `api_key`
  - `openai` / `openrouter` (OpenAI-compatible)
  - `anthropic` (messages API, tools mapped)
  - `gemini` (Google Generative Language)
- Auth: If you set `MCPO_API_KEY` in `.env`, `start.bat` will enforce Bearer on port 8000.

Example (OpenRouter auto provider):
```bash
curl -N -H "Authorization: Bearer $MCPO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openrouter/auto","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  http://localhost:8000/v1/chat/completions
```

Required keys (set in `.env`):
- `MCPO_API_KEY` (to lock down port 8000; optional but recommended)
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
- `ANTHROPIC_API_KEY` (for Claude/Anthropic)
- `GOOGLE_API_KEY` (for Gemini)

## 📍 Available Endpoints (Port 8001)

### Aggregate Endpoint (All Servers)
```
URL: http://localhost:8001/mcp
Type: Streamable HTTP (MCP)
Auth: None
Servers: All enabled servers
```

### Individual Server Endpoints
```
http://localhost:8001/perplexity
http://localhost:8001/time
http://localhost:8001/playwright
```

## 🔧 OpenWebUI Configuration

### Method 1: Add as MCP Server (Recommended)

1. Open OpenWebUI Settings
2. Navigate to **Settings → Tools → MCP**
3. Click **Add MCP Server**
4. Configure:
   ```
   Name: OpenHubUI Proxy
   URL: http://localhost:8001/mcp
   Type: Streamable HTTP
   Authentication: None (leave empty)
   ```

### Method 2: Add Individual Servers

For each server (perplexity, time, playwright):

```
Name: Time Server
URL: http://localhost:8001/time
Type: Streamable HTTP
Authentication: None
```

## 🚨 Common Connection Issues

### Issue 1: "Session ID" Authentication
**CLARIFICATION:** There is NO "Session ID" authentication mode for MCP endpoints.

The available auth options should be:
- **None** (default for port 8001)
- **Bearer Token** (if you manually add APIKeyMiddleware)
- **Basic Auth** (if you manually add APIKeyMiddleware)

### Issue 2: Port 8000 vs Port 8001
**Port 8000** (Admin/OpenAPI):
- Serves OpenAPI docs (`/docs`)
- Serves Admin UI (`/ui`)
- Serves Completions (`/v1/chat/completions`) for all providers via unified proxy
- Direct MCP mounts (`/{server-name}/openapi.json`)
- MAY require authentication if `--api-key` + `--strict-auth` flags are used

**Port 8001** (Streamable HTTP Proxy):
- Serves FastMCP Streamable HTTP
- OpenWebUI-compatible
- NO authentication by default
- This is what OpenWebUI needs

### Issue 3: "Connection Refused"
**Checklist:**
1. ✅ Is `start.bat` running? (Should see 2 windows)
2. ✅ Is port 8001 accessible? Test: `http://localhost:8001/_meta/logs/sources`
3. ✅ Are servers enabled in `mcpo_state.json`?
4. ✅ Is OpenWebUI using the CORRECT URL format?

### Issue 4: "No Tools Available"
**Possible causes:**
- Servers are disabled in `mcpo_state.json`
- Servers failed to start (check logs)
- OpenWebUI is connecting to wrong endpoint

**Check logs:**
```bash
# In PowerShell
Invoke-WebRequest -Uri "http://localhost:8001/_meta/logs" | ConvertFrom-Json
```

## 🧪 Testing Connection

### Test 1: Check if proxy is running
```powershell
Invoke-WebRequest -Uri "http://localhost:8001/_meta/logs/sources" -UseBasicParsing
```
**Expected:** JSON with list of servers

### Test 2: Test MCP endpoint (requires SSE client)
OpenWebUI will connect via Streamable HTTP protocol.

For manual testing, you'd need an MCP client that supports Streamable HTTP.

### Test 3: Check server health
```powershell
# Check if servers are enabled
Get-Content mcpo_state.json | ConvertFrom-Json
```

## 🔐 Authentication Modes Explained

### Current Architecture
```
┌─────────────────────────────────────────┐
│   Port 8000 (Admin/OpenAPI)             │
│   - API Key (Bearer/Basic) OPTIONAL     │
│   - Configured via --api-key flag       │
│   - Protected by --strict-auth flag     │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│   Port 8001 (Streamable HTTP)           │
│   - NO AUTHENTICATION by default        │
│   - OpenWebUI connects here             │
│   - FastMCP Streamable HTTP protocol    │
└─────────────────────────────────────────┘
```

### Adding Authentication to Port 8001 (If Needed)

**Option 1: Shared API Key (Simple)**
Modify `src/mcpo/proxy.py` line ~225:
```python
# Add APIKeyMiddleware to protect port 8001
api_key = os.getenv("MCPO_API_KEY")
if api_key:
    from mcpo.utils.auth import APIKeyMiddleware
    api_app.add_middleware(APIKeyMiddleware, api_key=api_key)
```

Then set in `.env`:
```
MCPO_API_KEY=top-secret
```

**Option 2: Per-Server Authentication**
Use FastMCP's built-in auth features (see FastMCP docs).

## 📝 OpenWebUI Connection String Examples

### Example 1: Aggregate (All Servers)
```
URL: http://localhost:8001/mcp
Headers: (none)
```

### Example 2: Single Server
```
URL: http://localhost:8001/time
Headers: (none)
```

### Example 3: With Optional Auth (if you added it)
```
URL: http://localhost:8001/mcp
Headers:
  Authorization: Bearer top-secret
```

## ✅ Verification Steps

1. **Start servers:** Run `start.bat`
2. **Check Admin UI:** Open `http://localhost:8000/ui`
   - Should see all 3 servers (perplexity, time, playwright)
   - All should show as "enabled"
3. **Check Proxy:** `http://localhost:8001/_meta/logs/sources`
   - Should return JSON with all servers listed
4. **Configure OpenWebUI:**
   - Add MCP Server
   - URL: `http://localhost:8001/mcp`
   - Auth: None
   - Save
5. **Test in OpenWebUI:** Try using a tool (e.g., "What time is it in New York?")

## 🆘 Still Not Working?

1. **Check logs:** Admin UI → Logs tab
2. **Check proxy logs:** `http://localhost:8001/_meta/logs`
3. **Verify config:** `mcpo.json` should have `"enabled": true` for each server
4. **Verify state:** `mcpo_state.json` should have `"enabled": true` under `server_states`
5. **Kill and restart:** Close all windows, run `start.bat` again

## 📞 Report Issue

If still failing, provide:
- OpenWebUI version
- Connection settings used (URL, auth type)
- Error message from OpenWebUI
- Output from: `http://localhost:8001/_meta/logs`
- Output from: `http://localhost:8000/_meta/servers`
