/**
 * MCP Tool Executor
 * 
 * ROLE:
 * The bridge to external tools provided by MCP Servers.
 * 
 * MECHANISM:
 * - Delegates the actual execution to the `McpClient` service.
 * - `McpClient` then routes the request via the appropriate transport (Stdio, etc.) to the external process.
 * 
 * SAFETY:
 * - Relies on `McpClient` to manage the connection state.
 * - Tools run in external processes, providing a degree of isolation, but the Agent trusts the MCP server's implementation.
 */

import { ServiceContainerImpl } from '../../core/service-container';
import { TYPES } from '../../core/types';
import { LoggingService } from '../../core/logging';
import { McpClient } from './mcp/mcp-client';
import type { JsonValue } from './mcp/mcp-types';
import type { ToolExecutor, ToolExecutionResult, ToolExecutionContext } from './tool-executor';

export class McpToolExecutor implements ToolExecutor {
  private serviceContainer: ServiceContainerImpl;
  private logger: LoggingService;

  constructor(serviceContainer: ServiceContainerImpl, logger: LoggingService) {
    this.serviceContainer = serviceContainer;
    this.logger = logger;
  }

  canExecute(toolName: string, origin: 'baseline' | 'panel' | 'mcp'): boolean {
    return origin === 'mcp';
  }

  async execute(toolName: string, args: Record<string, unknown>, _context?: ToolExecutionContext): Promise<ToolExecutionResult> {
    const startTime = Date.now();

    try {
      if (!toolName || typeof toolName !== 'string' || !toolName.trim()) {
        return {
          success: false,
          error: 'MCP tool name is required',
          duration_ms: Date.now() - startTime,
        };
      }

      const mcpClient = this.serviceContainer.get<McpClient>(TYPES.McpClient);

      if (!mcpClient || typeof mcpClient.callTool !== 'function') {
        return {
          success: false,
          error: 'MCP client service is not available',
          duration_ms: Date.now() - startTime,
        };
      }

      this.logger.info('[McpToolExecutor] Executing MCP tool', { toolName, args });

      const jsonArgs = args as Record<string, JsonValue>;
      const result = await mcpClient.callTool(toolName, jsonArgs);

      return {
        success: true,
        result,
        duration_ms: Date.now() - startTime,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error('[McpToolExecutor] Tool execution failed', error as Error, { toolName });

      return {
        success: false,
        error: `MCP error for ${toolName}: ${errorMessage}`,
        duration_ms: Date.now() - startTime,
      };
    }
  }
}

