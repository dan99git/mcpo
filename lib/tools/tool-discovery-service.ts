/**
 * Tool Discovery Service
 * 
 * ROLE:
 * The Aggregator. Responsible for gathering all currently available tools
 * from their various disparate sources and presenting a unified list to the Agent.
 * 
 * SOURCES:
 * 1. **Baseline**: Static list from `shared/baseline-tools` + `browser-automation` definitions.
 * 2. **Panel**: Dynamic list from `PanelRegistryService` based on the *active* panel ID.
 * 3. **MCP**: Dynamic list from `McpClient` based on connected servers.
 * 
 * ARCHITECTURE:
 * - Called on every agent interaction (or context switch) to build the `tools` array for the LLM.
 * - Normalizes all tool definitions into a standard OpenAI/OpenRouter compatible format (`ToolDefinition`).
 * 
 * CRITICAL LOGIC:
 * - `discoverTools(panelId)`: The main entry point. Merges all sources.
 * - `getPanelTools`: Accesses the active panel's manifest via the registry.
 * 
 * SAFETY:
 * - Read-only aggregator. No side effects.
 */
import { ServiceContainerImpl } from '../../core/service-container';
import { TYPES } from '../../core/types';
import { LoggingService } from '../../core/logging';
import { BROWSER_AUTOMATION_TOOLS } from './browser-automation/tool-definitions';
import { PanelRegistryService } from '../dynamic-panels/registry-service';
import { McpClient } from './mcp/mcp-client';
import { BASELINE_TOOLS } from '../../../../../shared/baseline-tools';
import type { PanelTool } from '../../../../../shared/panel-catalog';
import type { MCPServer, MCPTool } from './mcp/mcp-types';

export interface ToolDefinition {
  name: string;
  description: string;
  parameters?: {
    type: 'object';
    properties: Record<string, unknown>;
    required?: string[];
  };
}

export interface ToolOrigin {
  baseline: ToolDefinition[];
  panel: ToolDefinition[];
  mcp: ToolDefinition[];
  tool_origins: Record<string, 'baseline' | 'panel' | 'mcp'>;
}

export class ToolDiscoveryService {
  private logger: LoggingService;
  private serviceContainer: ServiceContainerImpl;

  constructor(logger: LoggingService, serviceContainer: ServiceContainerImpl) {
    this.logger = logger;
    this.serviceContainer = serviceContainer;
  }

  /**
   * Get baseline tools (always available)
   */
  getBaselineTools(): ToolDefinition[] {
    const baseline: ToolDefinition[] = BASELINE_TOOLS.map((tool) => ({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
    }));

    const environmentTools: ToolDefinition[] = BROWSER_AUTOMATION_TOOLS.map((tool) => ({
      name: tool.function.name,
      description: tool.function.description,
      parameters: tool.function.parameters as ToolDefinition['parameters'],
    }));

    return [...baseline, ...environmentTools];
  }

  /**
   * Get panel tools for a specific panel
   */
  getPanelTools(panelId: string | null): ToolDefinition[] {
    if (!panelId) {
      return [];
    }

    try {
      const panelRegistryService = this.serviceContainer.get<PanelRegistryService>(TYPES.PanelRegistryService);
      if (panelRegistryService && typeof panelRegistryService.getPanelTools === 'function') {
        const rawPanelTools = panelRegistryService.getPanelTools(panelId) || [];

        // Normalize panel tools to ToolDefinition format
        const mapped = rawPanelTools.map((tool: PanelTool) => {
          const name = tool?.name;
          if (!name) {
            return null;
          }

          // Convert panel manifest parameter ARRAY to OpenAI schema format
          let parameters: ToolDefinition['parameters'];
          
          if (Array.isArray(tool.parameters)) {
            // Panel manifest uses array format: [{ name, type, required, description }, ...]
            // Convert to OpenAI format: { type: 'object', properties: {...}, required: [...] }
            const properties: Record<string, unknown> = {};
            const required: string[] = [];
            
            for (const param of tool.parameters) {
              if (param.name) {
                const paramType = param.type || 'string';
                
                // Build the property schema
                const propSchema: Record<string, unknown> = {
                  type: paramType,
                  description: param.description || '',
                };
                
                // OpenAI requires 'items' for array types
                if (paramType === 'array') {
                  // Default to object items if not specified
                  propSchema.items = param.items || { type: 'object' };
                }
                
                // OpenAI requires specific format for object types
                if (paramType === 'object') {
                  propSchema.properties = param.properties || {};
                  // additionalProperties allows any structure if not defined
                  if (!param.properties) {
                    propSchema.additionalProperties = true;
                  }
                }
                
                properties[param.name] = propSchema;
                
                if (param.required) {
                  required.push(param.name);
                }
              }
            }
            
            parameters = {
              type: 'object',
              properties,
              required,
            };
            
            this.logger.debug('[ToolDiscoveryService] Converted panel tool parameters', {
              toolName: name,
              paramCount: tool.parameters.length,
              requiredCount: required.length,
            });
          } else if (tool.parameters && typeof tool.parameters === 'object') {
            // Already in OpenAI format (Record<string, unknown>)
            const params = tool.parameters as Record<string, unknown>;
            parameters = {
              type: 'object',
              properties: (params.properties as Record<string, unknown>) || params,
              required: (params.required as string[]) || [],
            };
          } else {
            parameters = {
              type: 'object',
              properties: {},
              required: [],
            };
          }

          const def: ToolDefinition = {
            name,
            description: tool.description || '',
            parameters,
          };
          return def;
        }).filter((t: ToolDefinition | null): t is ToolDefinition => t !== null);

        return mapped;
      }
    } catch (error) {
      this.logger.error('[ToolDiscoveryService] Failed to get panel tools', error, { panelId });
    }

    return [];
  }

  /**
   * Get MCP tools from connected servers
   */
  getMcpTools(): ToolDefinition[] {
    try {
      const mcpClient = this.serviceContainer.get<McpClient>(TYPES.McpClient);
      if (!mcpClient || typeof mcpClient.getAvailableTools !== 'function') {
        return [];
      }

      const allMcpTools = mcpClient.getAvailableTools() || [];

      // Filter to only include tools from currently connected servers
      const connectedServers = mcpClient.getAvailableServers?.() || [];
      const connectedServerNames = new Set(connectedServers.map((s: MCPServer) => s.name));

      const filteredTools = allMcpTools.filter((tool: MCPTool) => {
        // Tool names are in format "servername_toolname"
        const serverName = tool.name?.split(/[_.]/)[0];
        return connectedServerNames.has(serverName);
      });

      // Normalize MCP tools to ToolDefinition format
      const mapped = filteredTools.map((tool: MCPTool) => {
        const name = tool?.name;
        if (!name) {
          return null;
        }

        // MCPTool parameters is JsonSchema, convert to ToolDefinition format
        const params = tool.parameters;
        const def: ToolDefinition = {
          name,
          description: tool.description || '',
          parameters: {
            type: 'object',
            properties: (params?.properties as Record<string, unknown>) || {},
            required: params?.required || [],
          },
        };
        return def;
      }).filter((t: ToolDefinition | null): t is ToolDefinition => t !== null);

      return mapped;
    } catch (error) {
      this.logger.error('[ToolDiscoveryService] Failed to get MCP tools', error);
      return [];
    }
  }

  /**
   * Discover all tools for a given panel
   */
  discoverTools(panelId: string | null): ToolOrigin {
    const baselineTools = this.getBaselineTools();
    const panelTools = this.getPanelTools(panelId);
    const mcpTools = this.getMcpTools();

    // Build tool_origins map
    const tool_origins: Record<string, 'baseline' | 'panel' | 'mcp'> = {};

    baselineTools.forEach(tool => {
      tool_origins[tool.name] = 'baseline';
    });

    panelTools.forEach(tool => {
      tool_origins[tool.name] = 'panel';
    });

    mcpTools.forEach(tool => {
      tool_origins[tool.name] = 'mcp';
    });

    return {
      baseline: baselineTools,
      panel: panelTools,
      mcp: mcpTools,
      tool_origins,
    };
  }

  /**
   * Format tools for OpenRouter API (must include type: 'function')
   */
  formatToolsForOpenRouter(tools: ToolDefinition[]): Array<{
    type: 'function';
    function: {
      name: string;
      description: string;
      parameters: unknown;
    };
  }> {
    return tools.map(t => ({
      type: 'function' as const,
      function: {
        name: t.name,
        description: t.description || '',
        parameters: t.parameters || { type: 'object', properties: {}, required: [] }
      }
    }));
  }
}
