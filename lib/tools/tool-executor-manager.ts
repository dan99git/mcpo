/**
 * Tool Executor Manager
 * 
 * Routes tool execution to the appropriate executor based on origin
 */

import { ServiceContainerImpl } from '../../core/service-container';
import { LoggingService } from '../../core/logging';
import type { ToolExecutor, ToolExecutionResult, ToolExecutionContext } from './tool-executor';
import { McpToolExecutor } from './mcp-tool-executor';
import { PanelToolExecutor } from './panel-tool-executor';
import { BaselineToolExecutor } from './baseline-tool-executor';

export class ToolExecutorManager {
  private executors: ToolExecutor[] = [];
  private logger: LoggingService;
  private serviceContainer: ServiceContainerImpl;

  constructor(serviceContainer: ServiceContainerImpl, logger: LoggingService) {
    this.serviceContainer = serviceContainer;
    this.logger = logger;
    
    // Register executors
    this.executors.push(
      new BaselineToolExecutor(serviceContainer, logger),
      new McpToolExecutor(serviceContainer, logger),
      new PanelToolExecutor(serviceContainer, logger)
    );
  }

  /**
   * Execute a tool using the appropriate executor
   */
  async executeTool(
    toolName: string,
    origin: 'baseline' | 'panel' | 'mcp',
    args: Record<string, unknown>,
    context?: ToolExecutionContext
  ): Promise<ToolExecutionResult> {
    const start = Date.now();
    this.logger.debug('[ToolExecutorManager] Executing tool', { toolName, origin, hasContext: Boolean(context) });
    // Find executor that can handle this tool
    const executor = this.executors.find(e => e.canExecute(toolName, origin));
    
    if (!executor) {
      this.logger.warn('[ToolExecutorManager] No executor found for tool', { toolName, origin });
      return {
        success: false,
        error: `No executor available for tool "${toolName}" with origin "${origin}"`,
      };
    }

    // Execute tool
    const result = await executor.execute(toolName, args, context);
    if (result && result.duration_ms === undefined) {
      result.duration_ms = Date.now() - start;
    }
    return result;
  }

  /**
   * Register a custom executor
   */
  registerExecutor(executor: ToolExecutor): void {
    this.executors.push(executor);
  }
}

