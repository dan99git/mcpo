/**
 * Tool Executor Interfaces
 *
 * Defines interfaces for executing tools from different origins
 */

/*
 * AI Dev Notes:
 *  - `toolExecutor.ts` defines core TypeScript interfaces for tool execution across different origins (baseline, panel, MCP).
 *  - ToolExecutionResult captures success status, result data, error messages, and execution duration.
 *  - ToolExecutor interface provides canExecute() for capability checking and execute() for actual tool invocation.
 *  - ToolExecutionContext includes panelId, userId, and sessionId for execution context and logging.
 *  - Used by tool discovery service and AI chat endpoints to execute tools with proper error handling and metrics.
 */

export interface ToolExecutionResult {
  success: boolean;
  result?: unknown;
  error?: string;
  duration_ms?: number;
}

export interface ToolExecutor {
  canExecute(toolName: string, origin: 'baseline' | 'panel' | 'mcp'): boolean;
  execute(toolName: string, args: Record<string, unknown>, context?: ToolExecutionContext): Promise<ToolExecutionResult>;
}

export interface ToolExecutionContext {
  panelId?: string | null;
  userId?: string;
  sessionId?: string;
}
