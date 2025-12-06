# Tools Architecture

> **Complete guide to the AI Agent tool system**

This folder implements the tool infrastructure that allows the AI agent to discover, select, and execute actions within the application. Understanding this architecture is essential for adding new capabilities to the agent.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Tool Origins](#tool-origins)
   - [Baseline Tools](#baseline-tools)
   - [Panel Tools](#panel-tools)
   - [MCP Tools](#mcp-tools)
4. [Key Components](#key-components)
   - [ToolDiscoveryService](#tooldiscoveryservice)
   - [ToolExecutorManager](#toolexecutormanager)
   - [BaselineToolExecutor](#baselinetoolexecutor)
   - [PanelToolExecutor](#paneltoolexecutor)
   - [McpToolExecutor](#mcptoolexecutor)
5. [Frontend Integration](#frontend-integration)
   - [Browser Automation Flow](#browser-automation-flow)
   - [UnifiedAnnotationService](#unifiedannotationservice)
6. [Adding New Tools Guide](#adding-new-tools-guide)
   - [Adding a Baseline Tool](#adding-a-baseline-tool)
   - [Adding a Panel Tool](#adding-a-panel-tool)
   - [Adding an MCP Tool](#adding-an-mcp-tool)
   - [Adding an Environment Tool](#adding-an-environment-tool)
7. [Tool Definition Format](#tool-definition-format)
8. [Error Handling](#error-handling)
9. [Testing Tools](#testing-tools)

---

## System Overview

The tool system follows a **discovery → selection → routing → execution** pattern:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             TOOL LIFECYCLE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. DISCOVERY                    2. SELECTION                               │
│  ┌─────────────────────┐         ┌─────────────────────┐                   │
│  │ ToolDiscoveryService│────────▶│   LLM Agent         │                   │
│  │                     │  tools  │                     │                   │
│  │ • Baseline tools    │  list   │ Picks tool based on │                   │
│  │ • Panel tools       │         │ user intent and     │                   │
│  │ • MCP tools         │         │ tool descriptions   │                   │
│  └─────────────────────┘         └──────────┬──────────┘                   │
│                                             │                               │
│  3. ROUTING                      4. EXECUTION                               │
│  ┌─────────────────────┐         ┌──────────▼──────────┐                   │
│  │ ToolExecutorManager │◀────────│  Tool Call Request  │                   │
│  │                     │         │  { name, args }     │                   │
│  │ Routes by origin:   │         └─────────────────────┘                   │
│  │ • baseline → Baseline│                                                  │
│  │ • panel → Panel     │                                                   │
│  │ • mcp → MCP         │                                                   │
│  └──────────┬──────────┘                                                   │
│             │                                                               │
│  ┌──────────▼──────────────────────────────────────────────────────────┐   │
│  │                        EXECUTORS                                     │   │
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐        │   │
│  │  │ Baseline       │  │ Panel          │  │ MCP            │        │   │
│  │  │ ToolExecutor   │  │ ToolExecutor   │  │ ToolExecutor   │        │   │
│  │  │                │  │                │  │                │        │   │
│  │  │ • Navigation   │  │ • HTTP Proxy   │  │ • McpClient    │        │   │
│  │  │ • Environment  │  │ • Panel API    │  │ • External     │        │   │
│  │  │ • Activity Bar │  │ • Auto-inject  │  │   Processes    │        │   │
│  │  └────────────────┘  └────────────────┘  └────────────────┘        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture Diagram

### Full System Integration

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                  FRONTEND (apps/web)                              │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────┐    │
│   │                    DynamicPanelContainer.tsx                             │    │
│   │                                                                          │    │
│   │   ┌─────────────────────────────────────────────────────────────────┐   │    │
│   │   │              UnifiedAnnotationService                            │   │    │
│   │   │  ┌─────────────────────┐    ┌─────────────────────────────────┐ │   │    │
│   │   │  │ DynamicPanelAnnotator│    │ proxy-iframe Event System      │ │   │    │
│   │   │  │ (Local panels)       │    │ (External URLs via iframe)     │ │   │    │
│   │   │  └─────────────────────┘    └─────────────────────────────────┘ │   │    │
│   │   └─────────────────────────────────────────────────────────────────┘   │    │
│   │                              ▲                                           │    │
│   │                              │ WebSocket: environment.* commands         │    │
│   └──────────────────────────────┼───────────────────────────────────────────┘    │
│                                  │                                                │
└──────────────────────────────────┼────────────────────────────────────────────────┘
                                   │
        ═══════════════════════════╪═══════════════════════════════════════════════
                                   │ HTTP / WebSocket
        ═══════════════════════════╪═══════════════════════════════════════════════
                                   │
┌──────────────────────────────────┼────────────────────────────────────────────────┐
│                                  │        BACKEND (apps/api)                       │
├──────────────────────────────────▼────────────────────────────────────────────────┤
│                                                                                    │
│   ┌────────────────────────────────────────────────────────────────────────────┐  │
│   │                    /api/ai/environment/* Routes                             │  │
│   │                    (browser-automation/routes.ts)                           │  │
│   │                              │                                               │  │
│   │                              ▼                                               │  │
│   │               ┌──────────────────────────────┐                              │  │
│   │               │  BrowserAutomationService    │                              │  │
│   │               │  Sends WS commands to frontend│                              │  │
│   │               └──────────────────────────────┘                              │  │
│   └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                    │
│   ┌────────────────────────────────────────────────────────────────────────────┐  │
│   │                         Tool Infrastructure                                 │  │
│   │                                                                             │  │
│   │    ┌─────────────────┐    ┌─────────────────────────────────────────────┐  │  │
│   │    │ ToolDiscovery   │    │            ToolExecutorManager               │  │  │
│   │    │ Service         │    │                                              │  │  │
│   │    │                 │    │   ┌───────────┐┌───────────┐┌───────────┐   │  │  │
│   │    │ Aggregates:     │    │   │ Baseline  ││ Panel     ││ MCP       │   │  │  │
│   │    │ • BASELINE_TOOLS│    │   │ Executor  ││ Executor  ││ Executor  │   │  │  │
│   │    │ • BROWSER_AUTO  │    │   └───────────┘└───────────┘└───────────┘   │  │  │
│   │    │ • Panel manifests│   │                                              │  │  │
│   │    │ • MCP servers   │    └─────────────────────────────────────────────┘  │  │
│   │    └─────────────────┘                                                      │  │
│   └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Tool Origins

Tools come from three sources, each serving different purposes:

### Baseline Tools

**Location:** `shared/baseline-tools.ts`

System-level tools that are always available regardless of which panel is active. These handle:

- **Panel Navigation**: `open_home_panel`, `open_code_editor_panel`, etc.
- **Activity Bar Control**: `expand_activity_bar`, `set_activity_bar_devices`, etc.
- **Workspace State**: `get_workspace_state`, `get_active_panel`, `focus_surface`
- **Input Control**: `press_key`, `select_text`, `get_selection`, `hover_element`
- **Developer Feedback**: `developer_note`, `enhance_baseline_prompt`

**Example baseline tool definition:**

```typescript
{
  name: 'open_code_editor_panel',
  description: 'Open the Monaco-based Code Editor panel for in-browser editing',
  parameters: { type: 'object', properties: {}, required: [] },
}
```

### Panel Tools

**Location:** Panel manifests in `data/sites/*/panels/*/manifest.json`

Context-sensitive tools that only appear when a specific panel is active. Each panel declares its own tools in its manifest.

**Example panel manifest with tools:**

```json
{
  "panel_id": "code-editor",
  "panel_type": "code-editor",
  "title": "Code Editor",
  "tools": [
    {
      "name": "code_editor_read_file",
      "description": "Read the content of the currently open file",
      "endpoint": "GET /api/code-editor/file",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    },
    {
      "name": "code_editor_save_file",
      "description": "Save changes to the current file",
      "endpoint": "POST /api/code-editor/file/save",
      "parameters": {
        "type": "object",
        "properties": {
          "content": { "type": "string", "description": "File content to save" }
        },
        "required": ["content"]
      }
    }
  ]
}
```

**Key Features:**
- Tools are automatically discovered when their panel becomes active
- Endpoints are proxied through `PanelToolExecutor`
- The `filePath` parameter is auto-injected for code-editor tools

### MCP Tools

**Location:** Configured in `config/mcp-config.json`

External tools provided by Model Context Protocol servers. These run in separate processes and can provide specialized capabilities like filesystem access, database queries, or custom integrations.

**Example MCP configuration:**

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/filesystem-mcp", "/path/to/data"],
      "disabled": false
    }
  }
}
```

**MCP tools are:**
- Discovered by querying active MCP servers
- Executed via the `McpClient` service
- Run in isolated external processes

---

## Key Components

### ToolDiscoveryService

**File:** `toolDiscoveryService.ts`

Aggregates tools from all sources and normalizes them to a consistent format for the LLM.

```typescript
class ToolDiscoveryService {
  // Discover all available tools for a panel context
  async discoverTools(panelId?: string): Promise<{
    baseline: ToolDefinition[];
    panel: ToolDefinition[];
    mcp: ToolDefinition[];
    tool_origins: Record<string, 'baseline' | 'panel' | 'mcp'>;
  }>

  // Individual discovery methods
  getBaselineTools(): ToolDefinition[]
  async getPanelTools(panelId?: string): Promise<ToolDefinition[]>
  async getMcpTools(): Promise<ToolDefinition[]>
}
```

**Key responsibilities:**
1. Merge baseline tools from `BASELINE_TOOLS` and `BROWSER_AUTOMATION_TOOLS`
2. Load panel-specific tools from active panel's manifest
3. Query MCP servers for available tools
4. Build the `tool_origins` map used for routing

### ToolExecutorManager

**File:** `toolExecutorManager.ts`

Routes tool execution requests to the appropriate executor based on origin.

```typescript
class ToolExecutorManager {
  // Execute a tool with automatic routing
  async executeTool(
    toolName: string,
    origin: 'baseline' | 'panel' | 'mcp',
    args: Record<string, unknown>,
    context?: ToolExecutionContext
  ): Promise<ToolExecutionResult>

  // Add custom executors (extensibility point)
  registerExecutor(executor: ToolExecutor): void
}
```

### BaselineToolExecutor

**File:** `baselineToolExecutor.ts`

Handles execution of system tools. Routes to different subsystems:

| Tool Category | Route | Handler |
|--------------|-------|---------|
| Panel navigation | `/api/panels/open`, `/api/panels/close` | Panel controller |
| Environment/DOM | `/api/ai/environment/*` | BrowserAutomationService |
| Activity bar | `/api/ai/environment/activity-bar/*` | WebSocket to frontend |
| Prompt notes | `/api/ai/prompt-notes/append` | PromptNotesService |
| Developer notes | `/api/ai/developer-notes` | File-based storage |

### PanelToolExecutor

**File:** `panelToolExecutor.ts`

Proxies HTTP requests to panel-declared endpoints:

1. Looks up tool definition in panel manifest
2. Parses endpoint string (e.g., `GET /api/code-editor/file`)
3. Substitutes URL path parameters from args
4. Makes HTTP request with remaining args as body/query
5. Returns result with enhanced error extraction

**Special handling for code-editor:**
- Auto-injects `filePath` from panel context
- Handles file read/write operations

### McpToolExecutor

**File:** `mcpToolExecutor.ts`

Delegates to `McpClient` service for external tool execution:

```typescript
async execute(toolName: string, args: Record<string, unknown>): Promise<ToolExecutionResult> {
  const mcpClient = this.serviceContainer.get<McpClient>(TYPES.McpClient);
  const result = await mcpClient.callTool(toolName, jsonArgs);
  return { success: true, result };
}
```

---

## Frontend Integration

### Browser Automation Flow

When the agent needs to interact with the UI (click, type, read content), the flow is:

```
┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌─────────────┐
│ Agent calls │───▶│ Baseline     │───▶│ HTTP Route    │───▶│ WS Command  │
│ click_element│   │ ToolExecutor │    │ /environment/ │    │ to Frontend │
└─────────────┘    └──────────────┘    │ click         │    └──────┬──────┘
                                       └───────────────┘           │
                                                                   ▼
┌─────────────┐    ┌──────────────────────────────────────────────────────┐
│ Result      │◀───│           UnifiedAnnotationService                    │
│ returned    │    │                                                       │
└─────────────┘    │  Routes to DynamicPanelAnnotator OR proxy-iframe      │
                   │  based on panel type                                  │
                   └───────────────────────────────────────────────────────┘
```

### UnifiedAnnotationService

**File:** `apps/web/src/features/dynamic-panel/utils/browser-automation/UnifiedAnnotationService.ts`

A facade that unifies two annotation systems:

1. **DynamicPanelAnnotator** - For local React panels (full DOM access)
2. **proxy-iframe events** - For external URLs in iframes (cross-origin)

**Key methods:**
- `annotate()` - Mark interactive elements with numbered labels
- `click(annotationId)` - Click an element by its annotation ID
- `type(annotationId, text)` - Type into an input field
- `scroll(direction, target)` - Scroll the panel
- `getContent()` - Extract visible text
- `captureScreenshot()` - Take a screenshot
- `getAnnotations()` - Get cached annotation data

The service automatically detects panel type and routes to the appropriate system.

---

## Adding New Tools Guide

### Adding a Baseline Tool

Baseline tools are always available. Use for system-level capabilities.

**Step 1: Define the tool** in `shared/baseline-tools.ts`:

```typescript
export const BASELINE_TOOLS: BaselineTool[] = [
  // ... existing tools ...
  {
    name: 'my_new_tool',
    description: 'Clear description of what the tool does and when to use it',
    parameters: {
      type: 'object',
      properties: {
        myParam: {
          type: 'string',
          description: 'What this parameter controls',
        },
      },
      required: ['myParam'],
    },
  },
];
```

**Step 2: Implement the handler** in `baselineToolExecutor.ts`:

Add to the `executeBaseline()` method or create a helper:

```typescript
case 'my_new_tool': {
  const myParam = args.myParam as string;
  // Implementation
  const result = await this.postJson('/api/my-endpoint', { myParam });
  return { success: true, result };
}
```

**Step 3: Create backend route** (if needed):

If your tool calls an API endpoint, add the route in the appropriate controller.

### Adding a Panel Tool

Panel tools are context-sensitive. Use when the capability is specific to one panel.

**Step 1: Add to panel manifest:**

```json
{
  "panel_id": "my-panel",
  "tools": [
    {
      "name": "my_panel_action",
      "description": "Panel-specific action description",
      "endpoint": "POST /api/my-panel/action",
      "parameters": {
        "type": "object",
        "properties": {
          "actionData": { "type": "string" }
        },
        "required": ["actionData"]
      }
    }
  ]
}
```

**Step 2: Implement the API endpoint:**

```typescript
// In your panel's API routes
router.post('/api/my-panel/action', async (req, res) => {
  const { actionData } = req.body;
  // Implementation
  res.json({ success: true, result: '...' });
});
```

The `PanelToolExecutor` will automatically proxy requests to your endpoint.

### Adding an MCP Tool

MCP tools come from external servers. Use for integrations with external systems.

**Step 1: Configure the MCP server** in `config/mcp-config.json`:

```json
{
  "mcpServers": {
    "my-mcp-server": {
      "command": "node",
      "args": ["./my-mcp-server/index.js"],
      "disabled": false
    }
  }
}
```

**Step 2: Implement the MCP server:**

Follow the [MCP specification](https://modelcontextprotocol.io/) to create a server that exposes tools.

The `McpToolExecutor` and `McpClient` handle discovery and execution automatically.

### Adding an Environment Tool

Environment tools interact with the frontend UI. Use for DOM automation.

**Step 1: Define the tool** in `browser-automation/toolDefinitions.ts`:

```typescript
export const BROWSER_AUTOMATION_TOOLS: EnvironmentToolDefinition[] = [
  // ... existing tools ...
  {
    type: 'function',
    function: {
      name: 'my_dom_action',
      description: 'Performs an action on the page',
      parameters: {
        type: 'object',
        properties: {
          selector: { type: 'string' },
        },
        required: ['selector'],
      },
    },
  },
];
```

**Step 2: Add route handler** in `browser-automation/routes.ts`:

```typescript
router.post('/api/ai/environment/my-dom-action', async (req, res) => {
  const { selector } = req.body;
  const result = await browserAutomationService.sendCommand('my_dom_action', { selector });
  res.json({ success: true, data: result });
});
```

**Step 3: Handle in frontend** (DynamicPanelContainer or UnifiedAnnotationService):

```typescript
// In the WebSocket command handler
case 'my_dom_action': {
  const result = await unifiedService.myDomAction(args.selector);
  return { success: true, result };
}
```

### Adding a Panel Navigation Tool

> ⚠️ **CRITICAL ARCHITECTURE RULE**
> 
> Panel navigation tools (e.g., `open_home_panel`, `open_code_editor_panel`) are the **most duplicated**
> code pattern in the codebase. Follow these rules strictly to prevent regressions.

Panel navigation tools have a **single source of truth**: `shared/panel-navigation-map.ts`

**Step 1: Add to the shared map** in `shared/panel-navigation-map.ts`:

```typescript
// shared/panel-navigation-map.ts
export const PANEL_NAVIGATION_TOOL_MAP: Record<string, string> = {
  // ... existing entries ...
  'open_my_new_panel': 'my-new-panel',
};
```

**Step 2: Define the baseline tool** in `shared/baseline-tools.ts`:

```typescript
{
  name: 'open_my_new_panel',
  description: 'Open the My New Panel for XYZ functionality',
  parameters: { type: 'object', properties: {}, required: [] },
}
```

**Step 3: No additional code needed!**

The following files automatically use the shared map:
- `apps/api/src/features/tools/auralis-agent/auralisAgentOrchestrator.ts`
- `apps/web/src/features/auralis-chat/messageHandler.ts`
- `apps/web/src/features/tools/toolExecutionService.ts`
- `apps/api/src/features/tools/baselineToolExecutor.ts`

All of these import `getPanelIdForTool()` and `isPanelNavigationTool()` from the shared map.

**DO NOT:**
- Create local copies of the tool-to-panel-ID mapping
- Add switch/case statements for new panel tools in the executor files
- Hardcode panel IDs in multiple places

**Historical Note:** Before this consolidation (see commit history), the panel navigation map
was duplicated in 4+ locations, causing bugs when new panels were added and not all copies
were updated. The shared module pattern prevents this.

---

## Tool Definition Format

All tools are normalized to OpenAI function calling format:

```typescript
interface ToolDefinition {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: {
      type: 'object';
      properties: Record<string, {
        type: string;
        description?: string;
        enum?: string[];
        default?: unknown;
        items?: object; // For arrays
      }>;
      required?: string[];
    };
  };
}
```

**Best practices for descriptions:**
- Be specific about when to use the tool
- Include examples in the description
- Document expected return format
- Mention prerequisites (e.g., "Call annotate_page first")

---

## Error Handling

All executors return a consistent result type:

```typescript
interface ToolExecutionResult {
  success: boolean;
  result?: unknown;       // On success
  error?: string;         // On failure
  duration_ms?: number;   // Execution time
}
```

**Error handling patterns:**

1. **Validation errors** - Return immediately with descriptive message:
```typescript
if (!toolName) {
  return { success: false, error: 'Tool name is required' };
}
```

2. **Execution errors** - Catch and wrap with context:
```typescript
try {
  const result = await execute();
  return { success: true, result };
} catch (error) {
  return { 
    success: false, 
    error: `Failed to execute ${toolName}: ${error.message}` 
  };
}
```

3. **Timeout handling** - Use reasonable timeouts for external calls

---

## Testing Tools

### Manual Testing

1. **Test tool discovery:**
```bash
curl http://localhost:3001/api/ai/tools/discover
```

2. **Test tool execution:**
```bash
curl -X POST http://localhost:3001/api/ai/tools/execute \
  -H "Content-Type: application/json" \
  -d '{"tool": "open_code_editor_panel", "args": {}}'
```

### Automated Testing

Create tests in `tests/tools/`:

```typescript
describe('MyNewTool', () => {
  it('should execute successfully with valid args', async () => {
    const result = await executor.execute('my_new_tool', { myParam: 'value' });
    expect(result.success).toBe(true);
  });

  it('should fail gracefully with invalid args', async () => {
    const result = await executor.execute('my_new_tool', {});
    expect(result.success).toBe(false);
    expect(result.error).toContain('myParam');
  });
});
```

---

## File Structure Reference

```
apps/api/src/features/tools/
├── README.md                    # This file
├── toolDiscoveryService.ts      # Tool aggregation
├── toolExecutor.ts              # Executor interface
├── toolExecutorManager.ts       # Routing layer
├── baselineToolExecutor.ts      # System tools
├── panelToolExecutor.ts         # Panel-specific tools
├── mcpToolExecutor.ts           # MCP external tools
├── browser-automation/
│   ├── toolDefinitions.ts       # DOM automation tool definitions
│   ├── routes.ts                # HTTP endpoints for environment commands
│   └── BrowserAutomationService.ts  # WebSocket bridge to frontend
└── mcp/
    ├── McpClient.ts             # MCP server communication
    └── types.ts                 # MCP type definitions

shared/
├── baseline-tools.ts            # Baseline tool definitions
└── panel-navigation-map.ts      # ⚠️ SINGLE SOURCE OF TRUTH for panel navigation tool→ID mapping

apps/web/src/features/dynamic-panel/utils/browser-automation/
├── UnifiedAnnotationService.ts  # Frontend automation facade
├── DynamicPanelAnnotator.ts     # Local panel annotation
├── VirtualPointer.ts            # Click animation
├── TypingSimulator.ts           # Human-like typing
└── types.ts                     # Frontend types
```

---

## Quick Reference

| Task | File(s) to Edit |
|------|-----------------|
| Add always-available tool | `shared/baseline-tools.ts` + `baselineToolExecutor.ts` |
| **Add panel navigation tool** | `shared/panel-navigation-map.ts` + `shared/baseline-tools.ts` (**only!**) |
| Add panel-specific tool | Panel manifest + API route |
| Add DOM automation tool | `browser-automation/toolDefinitions.ts` + `routes.ts` + frontend handler |
| Add external integration | `config/mcp-config.json` + MCP server implementation |
| Change tool routing | `toolExecutorManager.ts` |
| Modify tool discovery | `toolDiscoveryService.ts` |

---

## API Routes Reference

### Tool Discovery & Execution
- `GET /api/ai/tools` - Discover available tools (baseline from `shared/baseline-tools.ts`)
- `POST /api/ai/tools/execute` - Execute baseline, panel, or MCP tool (accepts `panelId`)

### MCP Server Management
- `GET /api/ai/mcp-servers` - List MCP servers
- `POST /api/ai/mcp-servers/:name/toggle` - Enable/disable MCP server
- `POST /api/ai/mcp/call` - Execute MCP tool directly

### Environment Commands
- `POST /api/ai/environment/annotate` - Annotate page elements
- `POST /api/ai/environment/click` - Click annotated element
- `POST /api/ai/environment/type` - Type into annotated input
- `POST /api/ai/environment/scroll` - Scroll panel
- `POST /api/ai/environment/screenshot` - Capture screenshot
- `POST /api/ai/environment/content` - Get page content

---

## See Also

- [UnifiedAnnotationService Documentation](../../web/src/features/dynamic-panel/utils/browser-automation/UNIFIED_SERVICE.md)
- [Panel Manifest Schema](../panels/README.md)
- [MCP Specification](https://modelcontextprotocol.io/)
