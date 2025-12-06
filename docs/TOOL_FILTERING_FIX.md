# Tool Filtering Fix for Port 8001

## Problem
Tool enable/disable toggles work on port 8000 but NOT on port 8001 (FastMCP proxy).

FastMCP creates MCP sessions directly from the config and doesn't check StateManager for tool states.

## Solution
Filter disabled tools from MCP server configs before passing to FastMCP.

FastMCP doesn't have a built-in way to disable specific tools, so we need to intercept tool calls or filter them at the config level.

## Implementation Options

### Option 1: Modify MCP Server Config (Hacky)
- Some MCP servers support tool filtering via config
- Not standardized across all servers
- Would require server-specific logic

### Option 2: Wrapper Middleware (Complex)
- Intercept MCP protocol messages
- Filter tools/list responses
- Block disabled tool calls
- Requires deep MCP protocol knowledge

### Option 3: Separate Proxies per Server (Current Workaround)
- Individual server endpoints (`/time`, `/perplexity`) 
- Can be managed separately
- But aggregate (`/mcp`) exposes everything

### Option 4: Custom FastMCP Fork (Best Long-term)
- Modify FastMCP to support tool filtering
- Pass StateManager or filter function
- Most robust solution

## Recommended Workaround (Immediate)

Since FastMCP is a third-party library we don't control, the best immediate solution is to:

1. Document that tool filtering only works on port 8000 (direct OpenAPI)
2. For fine-grained control, use port 8000 direct endpoints
3. File issue/PR with FastMCP project for tool filtering support

## Long-term Solution

Implement a custom MCP proxy wrapper that:
1. Intercepts `tools/list` requests
2. Filters response based on StateManager
3. Blocks `tools/call` for disabled tools
4. Returns 403 for disabled tools
