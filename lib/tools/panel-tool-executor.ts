/**
 * Panel Tool Executor
 * 
 * ROLE:
 * Executes context-specific tools defined in Dynamic Panel manifests.
 * These tools are only available when their respective panel is active.
 * 
 * MECHANISM:
 * 1. Looks up the tool definition in `PanelRegistryService` using the `panelId` from context.
 * 2. Extracts the `endpoint` and `method` (GET/POST/etc.) from the manifest definition.
 * 3. Performs Parameter Substitution: Replaces URL placeholders (e.g., `/api/sites/{siteId}`) with arguments.
 * 4. Proxies the request to the internal API.
 * 
 * SPECIAL HANDLING:
 * - `enhance_*_prompt`: Intercepted locally to call `appendPromptNote`.
 * 
 * SAFETY:
 * - Sandboxed: Can only call endpoints explicitly allowed in the panel manifest.
 * - Context-Aware: Fails if `panelId` is missing or incorrect.
 */

import { ServiceContainerImpl } from '../../core/service-container';
import { TYPES } from '../../core/types';
import { LoggingService } from '../../core/logging';
import type { ToolExecutor, ToolExecutionResult, ToolExecutionContext } from './tool-executor';
import { appendPromptNote } from '../system-prompt/recursivePromptNotesService';
import { ConfigService } from '../../core/config-service';
import { getPanelContext } from '../dynamic-panels/code-editor/panel-context';
import type { PanelRegistryService } from '../dynamic-panels/registry-service';
import type { PanelTool, ToolParameter } from '../../../../../shared/panel-catalog';

export class PanelToolExecutor implements ToolExecutor {
  private serviceContainer: ServiceContainerImpl;
  private logger: LoggingService;

  constructor(serviceContainer: ServiceContainerImpl, logger: LoggingService) {
    this.serviceContainer = serviceContainer;
    this.logger = logger;
  }

  canExecute(toolName: string, origin: 'baseline' | 'panel' | 'mcp'): boolean {
    return origin === 'panel';
 }

  async execute(
    toolName: string,
    args: Record<string, unknown>,
    context?: ToolExecutionContext
  ): Promise<ToolExecutionResult> {
    const startTime = Date.now();
    const panelId = context?.panelId;

    if (!panelId) {
      return {
        success: false,
        error: 'Panel ID is required for panel tool execution',
        duration_ms: Date.now() - startTime,
      };
    }

    // Generic panel-level prompt enhancement tools: enhance_*_prompt
    if (toolName.startsWith('enhance_') && toolName.endsWith('_prompt')) {
      const rawNote = args.note;
      const note = typeof rawNote === 'string' ? rawNote.trim() : '';

      if (!note) {
        return {
          success: false,
          error: 'note is required and must be a non-empty string',
          duration_ms: Date.now() - startTime,
        };
      }

      try {
        await appendPromptNote(panelId, note);
        return {
          success: true,
          result: {
            scope: 'panel',
            panel_id: panelId,
          },
          duration_ms: Date.now() - startTime,
        };
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : 'Unknown error';
        this.logger.error('[PanelToolExecutor] Failed to append prompt note', error, { panelId, toolName });
        return {
          success: false,
          error: errorMessage,
          duration_ms: Date.now() - startTime,
        };
      }
    }

    try {
      const panelRegistryService = this.serviceContainer.get<PanelRegistryService>(TYPES.PanelRegistryService);
      const configService = this.serviceContainer.get<ConfigService>(TYPES.ConfigService);

      if (!panelRegistryService || !configService) {
        return {
          success: false,
          error: 'Panel registry service is not available',
          duration_ms: Date.now() - startTime,
        };
      }

      const tools: PanelTool[] = panelRegistryService.getPanelTools(panelId) || [];
      const definition = tools.find((t) => t.name === toolName);

      if (!definition) {
        return {
          success: false,
          error: `Tool "${toolName}" not found for panel ${panelId}`,
          duration_ms: Date.now() - startTime,
        };
      }

      const method = (definition.method || 'POST').toUpperCase();
      let endpoint = definition.endpoint as string | undefined;

      if (!endpoint) {
        return {
          success: false,
          error: `Tool "${toolName}" is missing an endpoint`,
          duration_ms: Date.now() - startTime,
        };
      }

      // AUTO-INJECT: For code-editor panel tools, auto-fill filePath from panel context if missing
      // This ensures tools work even if the AI doesn't explicitly pass the filePath
      if (panelId === 'code-editor' && toolName.startsWith('code_editor_')) {
        const editorContext = getPanelContext('code-editor');
        
        if (editorContext) {
          // Auto-fill filePath if not provided (handles undefined, null, empty string)
          if ((!args.filePath || args.filePath === null || args.filePath === '') && editorContext.filePath) {
            args.filePath = editorContext.filePath;
          }
          // Auto-fill workspaceRoot if not provided  
          if ((!args.workspaceRoot || args.workspaceRoot === null) && editorContext.workspaceRoot) {
            args.workspaceRoot = editorContext.workspaceRoot;
          }
          // NOTE: We do NOT auto-fill 'contents', 'oldString', 'newString', 'edits' etc.
          // The model MUST provide what it wants to write/change - we can only help with filePath
        }
      }

      // Validate required parameters before making the API call
      const params = Array.isArray(definition.parameters) ? definition.parameters as ToolParameter[] : [];
      const requiredParams = params
        .filter((p) => p.required)
        .map((p) => p.name);
      
      const missingParams = requiredParams.filter((param) => {
        const value = args[param];
        return value === undefined || value === null || value === '';
      });

      if (missingParams.length > 0) {
        return {
          success: false,
          error: `Missing required parameter(s): ${missingParams.join(', ')}. Please provide ${missingParams.map((p) => `"${p}"`).join(' and ')} to use this tool.`,
          duration_ms: Date.now() - startTime,
        };
      }

      // Replace template params like /api/scenes/{id}
      endpoint = endpoint.replace(/\{([^}]+)\}/g, (_match, key) => {
        const value = args[key];
        return value !== undefined ? String(value) : '';
      });

      const port = configService?.get('port') ?? 7085;
      const baseUrl = `http://127.0.0.1:${port}`;
      const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`;

      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      let response: Response;

      this.logger.debug('[PanelToolExecutor] Executing panel tool', { 
        toolName, 
        panelId, 
        endpoint,
        method,
        argKeys: Object.keys(args)
      });

      if (method === 'GET' || method === 'DELETE') {
        const urlObj = new URL(url);
        Object.entries(args || {}).forEach(([key, value]) => {
          if (value !== undefined && value !== null) {
            urlObj.searchParams.set(key, String(value));
          }
        });
        response = await fetch(urlObj.toString(), { method, headers });
      } else {
        response = await fetch(url, {
          method,
          headers,
          body: JSON.stringify(args || {}),
        });
      }

      const payload = (await response.json().catch(() => ({}))) as { 
        success?: boolean; 
        error?: string; 
        data?: unknown;
        hint?: string;
        action_required?: string;
        lineNumber?: number;
        actualText?: string;
        matchLocations?: number[];
      };
      const payloadSuccess = typeof payload.success === 'boolean' ? payload.success : undefined;
      const payloadError = typeof payload.error === 'string' ? payload.error : undefined;
      const payloadData = payload.data ?? payload;

      if (!response.ok || payloadSuccess === false) {
        // Extract all helpful error details from the response
        const errorParts: string[] = [];
        if (payloadError) errorParts.push(payloadError);
        if (payload.hint) errorParts.push(`Hint: ${payload.hint}`);
        if (payload.action_required) errorParts.push(`Action: ${payload.action_required}`);
        if (payload.lineNumber) errorParts.push(`Line: ${payload.lineNumber}`);
        if (payload.matchLocations) errorParts.push(`Match locations: lines ${payload.matchLocations.join(', ')}`);
        if (payload.actualText) errorParts.push(`Actual text in file:\n${payload.actualText}`);
        
        const errorText = errorParts.length > 0 
          ? errorParts.join('\n')
          : `${response.status} ${response.statusText}`;
        throw new Error(errorText);
      }

      return {
        success: true,
        result: payloadData,
        duration_ms: Date.now() - startTime,
      };
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      this.logger.error('[PanelToolExecutor] Tool execution failed', error as Error, { panelId, toolName });
      return {
        success: false,
        error: errorMessage,
        duration_ms: Date.now() - startTime,
      };
    }
  }
}
