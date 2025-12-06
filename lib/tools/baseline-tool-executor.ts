/**
 * Baseline Tool Executor
 * 
 * ============================================================================
 * ARCHITECTURE NOTES
 * ============================================================================
 * 
 * ROLE:
 * The "System" executor. Handles tools that are native to the Auralis environment
 * and do not belong to a specific dynamic panel or external MCP server.
 * 
 * CAPABILITIES:
 * 1. **Panel Navigation**: Opening panels (`open_home_screen`).
 *    - Implementation: POSTs to `/api/panels/open`.
 *    - Panel ID mapping uses shared/panel-navigation-map.ts
 * 2. **Browser Automation**: "Virtual User" actions (`click_element`, `type_text`).
 *    - Implementation: POSTs to `/api/ai/environment/*`.
 * 3. **Prompt Notes**: Self-correction (`enhance_baseline_prompt`).
 *    - Implementation: POSTs to `/api/ai/prompt-notes/append`.
 * 
 * KEY DEPENDENCIES:
 * - PANEL_NAVIGATION_TOOL_MAP: Imported from shared/panel-navigation-map.ts
 *   The executePanelTool() method uses this to resolve panel IDs.
 * - BASELINE_TOOLS: Imported from shared/baseline-tools.ts for tool definitions
 * - BROWSER_AUTOMATION_TOOLS: DOM interaction tool definitions
 * 
 * MODIFICATION RULES:
 * - When adding new panel navigation tools, update shared/panel-navigation-map.ts
 * - When adding new baseline tools, update shared/baseline-tools.ts
 * - Environment tools are defined in browser-automation/toolDefinitions.ts
 * 
 * SAFETY:
 * - Validates inputs (e.g., `annotation_id` must be a number).
 * - All actions are proxied through the API layer, inheriting its auth/validation.
 * 
 * ============================================================================
 */

import { readFile } from 'fs/promises';
import { join } from 'path';
import { ConfigService } from '../../core/config-service';
import { ServiceContainerImpl } from '../../core/service-container';
import { TYPES } from '../../core/types';
import { LoggingService } from '../../core/logging';
import { BASELINE_TOOLS } from '../../../../../shared/baseline-tools';
import { BROWSER_AUTOMATION_TOOLS } from './browser-automation/tool-definitions';

// CRITICAL: Import panel navigation map from shared source
// See shared/panel-navigation-map.ts for rules on adding new panels
import { getPanelIdForTool } from '../../../../../shared/panel-navigation-map';

import type { ToolExecutor, ToolExecutionResult, ToolExecutionContext } from './tool-executor';

export class BaselineToolExecutor implements ToolExecutor {
  private readonly serviceContainer: ServiceContainerImpl;
  private readonly logger: LoggingService;
  private readonly apiBaseUrl: string;

  constructor(serviceContainer: ServiceContainerImpl, logger: LoggingService) {
    this.serviceContainer = serviceContainer;
    this.logger = logger;

    // Resolve API base URL from configured HTTP port
    const configService = this.serviceContainer.get<ConfigService>(TYPES.ConfigService);
    const port = configService?.get('port') ?? 7085;
    this.apiBaseUrl = `http://127.0.0.1:${port}`;
  }

  canExecute(toolName: string, origin: 'baseline' | 'panel' | 'mcp'): boolean {
    return origin === 'baseline';
  }

  async execute(toolName: string, args: Record<string, unknown>, _context?: ToolExecutionContext): Promise<ToolExecutionResult> {
    const start = Date.now();

    try {
      // Panel navigation tools (open_*_panel, get_active_panel)
      const panelResult = await this.executePanelTool(toolName, args);
      if (panelResult !== null) {
        return {
          success: true,
          result: panelResult,
          duration_ms: Date.now() - start,
        };
      }

      // Prompt notes baseline tool
      if (toolName === 'enhance_baseline_prompt') {
        const result = await this.executeEnhanceBaselinePrompt(args);
        return {
          success: true,
          result,
          duration_ms: Date.now() - start,
        };
      }

      // Error log access tool
      if (toolName === 'get_error_logs') {
        const result = await this.executeGetErrorLogs(args);
        return {
          success: true,
          result,
          duration_ms: Date.now() - start,
        };
      }

      // Agentic workflow continuation tool
      if (toolName === 'continue_workflow') {
        const reason = args.reason as string || 'Continuing workflow';
        const context = args.context as string | undefined;
        this.logger.info('[BaselineToolExecutor] continue_workflow called', { reason, hasContext: !!context });
        return {
          success: true,
          result: {
            _continue: true,
            reason,
            context,
            message: `Continuing: ${reason}`,
          },
          duration_ms: Date.now() - start,
        };
      }

      // Environment / DOM tools
      const envResult = await this.executeEnvironmentTool(toolName, args);
      if (envResult !== null) {
        return {
          success: true,
          result: envResult,
          duration_ms: Date.now() - start,
        };
      }

      // Unknown baseline tool
      this.logger.warn('[BaselineToolExecutor] Unknown baseline tool', { toolName, args });
      const knownBaselineTools = [
        ...BASELINE_TOOLS.map((t) => t.name),
        ...BROWSER_AUTOMATION_TOOLS.map((t) => t.function.name),
      ].sort();
      return {
        success: false,
        error:
          `Unknown baseline tool: ${toolName}. Known baseline tools: ${knownBaselineTools.join(', ')}`,
        duration_ms: Date.now() - start,
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.logger.error('[BaselineToolExecutor] Tool execution failed', error as Error, {
        toolName,
      });
      return {
        success: false,
        error: message,
        duration_ms: Date.now() - start,
      };
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Validation Helpers (private methods to eliminate duplication)
  // ────────────────────────────────────────────────────────────────────────────

  /**
   * Validates and extracts annotation_id from tool arguments.
   * Accepts both snake_case (annotation_id) and camelCase (annotationId) for API flexibility.
   */
  private parseAnnotationId(args: Record<string, unknown>): number {
    const rawId = (args.annotation_id ?? args.annotationId) as number | string | undefined;
    const annotationId = typeof rawId === 'string' ? Number(rawId) : rawId;
    if (!Number.isFinite(annotationId as number)) {
      throw new Error('annotation_id (or annotationId) is required and must be a number');
    }
    return annotationId as number;
  }

  /**
   * Validates direction argument for scroll operations.
   */
  private parseDirection(args: Record<string, unknown>): 'up' | 'down' {
    const direction = args.direction;
    if (direction !== 'up' && direction !== 'down') {
      throw new Error('direction must be "up" or "down"');
    }
    return direction;
  }

  /**
   * Extracts error message from API response data.
   */
  private getErrorMessage(data: Record<string, unknown> | null, fallback: string): string {
    if (!data) return fallback;
    const error = data.error ?? data.message;
    return typeof error === 'string' ? error : fallback;
  }

  // ────────────────────────────────────────────────────────────────────────────
  // HTTP Helpers
  // ────────────────────────────────────────────────────────────────────────────

  private buildUrl(path: string): string {
    if (!path.startsWith('/')) {
      return `${this.apiBaseUrl}/${path}`;
    }
    return `${this.apiBaseUrl}${path}`;
  }
  private async postJson(
    path: string,
    body: Record<string, unknown> | undefined
  ): Promise<Record<string, unknown>> {
    const url = this.buildUrl(path);

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: body ? JSON.stringify(body) : undefined,
    });

    let payload: Record<string, unknown> | null = null;
    try {
      payload = await response.json() as Record<string, unknown>;
    } catch {
      // Ignore JSON parse errors; will fall back to statusText below
    }

    if (!response.ok) {
      const errorText =
        (payload && (payload.error || payload.message)) || `${response.status} ${response.statusText}`;
      throw new Error(String(errorText));
    }

    return payload ?? {};
  }

  private async getJson(path: string): Promise<Record<string, unknown>> {
    const url = this.buildUrl(path);

    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    let payload: Record<string, unknown> | null = null;
    try {
      payload = await response.json() as Record<string, unknown>;
    } catch {
      // Ignore JSON parse errors; will fall back to statusText below
    }

    if (!response.ok) {
      const errorText =
        (payload && (payload.error || payload.message)) || `${response.status} ${response.statusText}`;
      throw new Error(String(errorText));
    }

    return payload ?? {};
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Panel navigation + browser panels
  // Uses shared/panel-navigation-map.ts for tool → panelId resolution
  // ────────────────────────────────────────────────────────────────────────────

  private async executePanelTool(toolName: string, args: Record<string, unknown>): Promise<unknown | null> {
    // Helper to open a panel via POST /api/panels/open
    const openPanel = async (panelId: string, context: Record<string, unknown> = {}): Promise<unknown> => {
      const body = {
        panel_id: panelId,
        context,
        source: 'ai-agent',
      } as Record<string, unknown>;

      const data = await this.postJson('/api/panels/open', body);

      return {
        success: true,
        message: data.message || `Panel "${panelId}" opened successfully`,
        panel: data.panel,
        mcpToggles: data.mcpToggles,
      };
    };

    // ──────────────────────────────────────────────────────────────────────────
    // PANEL NAVIGATION TOOLS
    // Uses shared getPanelIdForTool() - see shared/panel-navigation-map.ts
    // DO NOT add new panel cases below - add to the shared map instead
    // ──────────────────────────────────────────────────────────────────────────
    const panelId = getPanelIdForTool(toolName);
    if (panelId) {
      return openPanel(panelId);
    }

    // ──────────────────────────────────────────────────────────────────────────
    // NON-NAVIGATION PANEL TOOLS
    // These require special handling beyond simple panel opening
    // ──────────────────────────────────────────────────────────────────────────
    switch (toolName) {
      case 'get_active_panel': {
        const data = await this.getJson('/api/panels/active');
        return {
          success: true,
          active_panel: data.active_panel,
        };
      }

      default:
        return null;
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Prompt notes (baseline)
  // ────────────────────────────────────────────────────────────────────────────

  private async executeEnhanceBaselinePrompt(args: Record<string, unknown>): Promise<{ success: boolean; scope: string }> {
    const rawNote = args.note;
    const note = typeof rawNote === 'string' ? rawNote.trim() : '';

    if (!note) {
      throw new Error('note is required and must be a non-empty string');
    }

    const body = {
      panel_id: 'baseline',
      note,
    } as const;

    const data = await this.postJson('/api/ai/prompt-notes/append', body);

    if (!data || data.success === false) {
      throw new Error(this.getErrorMessage(data, 'Failed to append baseline prompt note'));
    }

    return {
      success: true,
      scope: 'baseline',
    };
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Error log access (debugging tool)
  // ────────────────────────────────────────────────────────────────────────────

  private async executeGetErrorLogs(args: Record<string, unknown>): Promise<{
    success: boolean;
    logType: string;
    lineCount: number;
    entries: string[];
    totalLines: number;
    filtered: boolean;
  }> {
    // Parse and validate arguments
    const rawLineCount = args.lineCount as number | undefined;
    const lineCount = Math.min(Math.max(rawLineCount ?? 50, 1), 500);
    
    const logType = (args.logType as string) ?? 'error';
    const validLogTypes = ['error', 'exceptions', 'rejections', 'combined'];
    if (!validLogTypes.includes(logType)) {
      throw new Error(`Invalid logType: ${logType}. Valid options: ${validLogTypes.join(', ')}`);
    }

    const searchPattern = typeof args.searchPattern === 'string' ? args.searchPattern.trim() : '';

    // Resolve log file path
    const logDir = join(process.cwd(), 'logs');
    const logFileMap: Record<string, string> = {
      error: 'error.log',
      exceptions: 'exceptions.log',
      rejections: 'rejections.log',
      combined: 'combined.log',
    };
    const logFilePath = join(logDir, logFileMap[logType]);

    // Read log file
    let fileContent: string;
    try {
      fileContent = await readFile(logFilePath, 'utf-8');
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code === 'ENOENT') {
        return {
          success: true,
          logType,
          lineCount: 0,
          entries: [],
          totalLines: 0,
          filtered: !!searchPattern,
        };
      }
      throw new Error(`Failed to read log file: ${(err as Error).message}`);
    }

    // Split into lines and filter
    let lines = fileContent.split('\n').filter(line => line.trim());
    const totalLines = lines.length;

    // Apply search filter if provided
    if (searchPattern) {
      const pattern = new RegExp(searchPattern, 'i');
      lines = lines.filter(line => pattern.test(line));
    }

    // Get last N entries
    const entries = lines.slice(-lineCount);

    return {
      success: true,
      logType,
      lineCount: entries.length,
      entries,
      totalLines,
      filtered: !!searchPattern,
    };
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Environment / DOM tools (dynamic panel container)
  // ────────────────────────────────────────────────────────────────────────────

  private async executeEnvironmentTool(toolName: string, args: Record<string, unknown>): Promise<unknown | null> {
    switch (toolName) {
      case 'annotate_page': {
        const data = await this.postJson('/api/ai/environment/annotate', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to annotate dynamic panel container'));
        }
        return data.data;
      }
      case 'annotate_activity_bar': {
        const data = await this.postJson('/api/ai/environment/activity-bar/annotate', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to annotate activity bar'));
        }
        return data.data;
      }

      case 'click_element': {
        const annotationId = this.parseAnnotationId(args);

        const body = {
          annotationId,
          animate: args.animate,
        } as Record<string, unknown>;

        const data = await this.postJson('/api/ai/environment/click', body);
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to click element in dynamic panel container'));
        }
        return data.data;
      }
      case 'click_activity_bar_item': {
        const annotationId = this.parseAnnotationId(args);

        const data = await this.postJson('/api/ai/environment/activity-bar/click', {
          annotationId,
          animate: args.animate,
        });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to click activity bar item'));
        }
        return data.data;
      }

      case 'type_text': {
        const annotationId = this.parseAnnotationId(args);
        const text = typeof args.text === 'string' ? args.text : '';

        if (!text) {
          throw new Error('text is required');
        }

        const body = { annotationId, text };
        const data = await this.postJson('/api/ai/environment/type', body);
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to type text in dynamic panel container'));
        }
        return data.data;
      }

      case 'navigate_to': {
        const url = typeof args.url === 'string' ? args.url : '';
        if (!url) {
          throw new Error('url is required');
        }

        const body = { url };
        const data = await this.postJson('/api/ai/environment/navigate', body);
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to navigate dynamic panel container'));
        }
        return data.data;
      }

      case 'get_page_content': {
        const data = await this.postJson('/api/ai/environment/content', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to get page content from dynamic panel container'));
        }
        return data.data;
      }

      case 'capture_screenshot': {
        const data = await this.postJson('/api/ai/environment/screenshot', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to capture screenshot of dynamic panel container'));
        }
        return data.data;
      }

      case 'scroll': {
        const direction = this.parseDirection(args);

        const target = typeof args.target === 'string' ? args.target : undefined;
        const body: Record<string, unknown> = { direction };
        if (target) {
          body.target = target;
        }

        const data = await this.postJson('/api/ai/environment/scroll', body);
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to scroll dynamic panel container'));
        }
        return data.data;
      }
      case 'scroll_activity_bar': {
        const direction = this.parseDirection(args);

        const data = await this.postJson('/api/ai/environment/activity-bar/scroll', {
          direction,
        });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to scroll activity bar'));
        }
        return data.data;
      }

      case 'wait': {
        const secondsValue = args.seconds as number | string | undefined;
        const seconds = typeof secondsValue === 'string' ? Number(secondsValue) : secondsValue ?? 2;

        if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 30) {
          throw new Error('seconds must be between 0 and 30');
        }

        const body = { seconds };
        const data = await this.postJson('/api/ai/environment/wait', body);
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to wait in dynamic panel container'));
        }
        return data.data;
      }

      case 'go_back': {
        const data = await this.postJson('/api/ai/environment/back', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to go back in dynamic panel container'));
        }
        return data.data;
      }

      // ────────────────────────────────────────────────────────────────────────
      // Activity Bar collapse/expand tools
      // These are forwarded to the frontend via WebSocket since they control UI state
      // ────────────────────────────────────────────────────────────────────────

      case 'expand_activity_bar': {
        const data = await this.postJson('/api/ai/environment/activity-bar/collapse', { collapsed: false });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to expand activity bar'));
        }
        return data.data ?? { success: true, collapsed: false };
      }

      case 'collapse_activity_bar': {
        const data = await this.postJson('/api/ai/environment/activity-bar/collapse', { collapsed: true });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to collapse activity bar'));
        }
        return data.data ?? { success: true, collapsed: true };
      }

      case 'set_activity_bar_collapsed': {
        const collapsed = args.collapsed === true || args.collapsed === 'true';
        const data = await this.postJson('/api/ai/environment/activity-bar/collapse', { collapsed });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to set activity bar collapsed state'));
        }
        return data.data ?? { success: true, collapsed };
      }

      case 'set_activity_bar_site_manager': {
        const data = await this.postJson('/api/ai/environment/activity-bar/view', { view: 'real-sites' });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to switch to site manager view'));
        }
        return data.data ?? { success: true, view: 'real-sites' };
      }

      case 'set_activity_bar_devices': {
        const data = await this.postJson('/api/ai/environment/activity-bar/view', { view: 'devices' });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to switch to devices view'));
        }
        return data.data ?? { success: true, view: 'devices' };
      }

      case 'set_activity_bar_file_explorer': {
        const data = await this.postJson('/api/ai/environment/activity-bar/view', { view: 'files' });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to switch to file explorer view'));
        }
        return data.data ?? { success: true, view: 'files' };
      }

      case 'set_activity_bar_site_manager_mock': {
        const data = await this.postJson('/api/ai/environment/activity-bar/view', { view: 'sites' });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to switch to mock site manager view'));
        }
        return data.data ?? { success: true, view: 'sites' };
      }

      // === State & Focus tools ===
      case 'get_activity_bar_state': {
        const data = await this.getJson('/api/ai/environment/activity-bar/state');
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to get activity bar state'));
        }
        return data.data;
      }

      case 'get_workspace_state': {
        const data = await this.getJson('/api/ai/environment/workspace/state');
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to get workspace state'));
        }
        return data.data;
      }

      case 'focus_surface': {
        const surface = args.surface as string | undefined;
        if (surface !== 'activity_bar' && surface !== 'dynamic_panel') {
          throw new Error('surface must be "activity_bar" or "dynamic_panel"');
        }

        const data = await this.postJson('/api/ai/environment/focus-surface', { surface });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to focus surface'));
        }
        return data.data ?? { success: true, surface };
      }

      // === File Editor tools ===
      case 'open_file_in_editor': {
        const filePath = args.filePath as string | undefined;
        const readOnly = args.readOnly === true;

        if (!filePath || typeof filePath !== 'string') {
          throw new Error('filePath is required and must be a string');
        }

        const data = await this.postJson('/api/ai/environment/open-file-in-editor', { filePath, readOnly });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to open file in editor'));
        }
        return data.data ?? { success: true, filePath, readOnly };
      }

      // === Developer Note tool ===
      case 'developer_note': {
        const category = args.category as string | undefined;
        const title = args.title as string | undefined;
        const details = args.details as string | undefined;
        const relatedTools = args.related_tools as string[] | undefined;
        const relatedPanels = args.related_panels as string[] | undefined;
        const severity = (args.severity as string) || 'medium';

        if (!category || !title || !details) {
          throw new Error('category, title, and details are required');
        }

        // Build filename from timestamp and title
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const sanitizedTitle = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 50);
        const filename = `${timestamp}-${category}-${sanitizedTitle}.md`;

        // Build content in markdown format
        const contentParts = [
          `# ${title}`,
          '',
          `**Category:** ${category}`,
          `**Severity:** ${severity}`,
          `**Timestamp:** ${new Date().toISOString()}`,
          '',
        ];

        if (relatedTools && relatedTools.length > 0) {
          contentParts.push(`**Related Tools:** ${relatedTools.join(', ')}`);
        }
        if (relatedPanels && relatedPanels.length > 0) {
          contentParts.push(`**Related Panels:** ${relatedPanels.join(', ')}`);
        }

        contentParts.push('', '## Details', '', details);
        const content = contentParts.join('\n');

        const data = await this.postJson('/api/ai/developer-note', {
          filename,
          content,
          category,
          severity,
          title,
        });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to save developer note'));
        }
        return data;
      }

      // === Keyboard & Input Control Tools ===
      case 'press_key': {
        const key = args.key as string | undefined;
        const modifiers = args.modifiers as string[] | undefined;

        if (!key || typeof key !== 'string') {
          throw new Error('key is required and must be a string');
        }

        const data = await this.postJson('/api/ai/environment/press-key', {
          key,
          modifiers: modifiers ?? [],
        });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to send key press'));
        }
        return data.data ?? { key, modifiers };
      }

      case 'select_text': {
        const annotationId = args.annotation_id !== undefined ? Number(args.annotation_id) : undefined;
        const startLine = args.startLine !== undefined ? Number(args.startLine) : undefined;
        const startColumn = args.startColumn !== undefined ? Number(args.startColumn) : undefined;
        const endLine = args.endLine !== undefined ? Number(args.endLine) : undefined;
        const endColumn = args.endColumn !== undefined ? Number(args.endColumn) : undefined;
        const selectAll = args.selectAll === true;

        const data = await this.postJson('/api/ai/environment/select-text', {
          annotationId,
          startLine,
          startColumn,
          endLine,
          endColumn,
          selectAll,
        });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to select text'));
        }
        return data.data ?? { selectAll };
      }

      case 'get_selection': {
        const data = await this.postJson('/api/ai/environment/get-selection', {});
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to get selection'));
        }
        return data.data ?? { text: '', source: 'unknown' };
      }

      case 'hover_element': {
        const annotationId = this.parseAnnotationId(args);
        const data = await this.postJson('/api/ai/environment/hover', { annotationId });
        if (!data || data.success === false) {
          throw new Error(this.getErrorMessage(data, 'Failed to hover element'));
        }
        return data.data ?? { annotationId };
      }

      default:
        return null;
    }
  }
}
