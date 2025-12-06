/**
 * Auralis Agent Orchestrator
 * 
 * ============================================================================
 * ARCHITECTURE NOTES
 * ============================================================================
 * 
 * This module is the backend-side agent loop that:
 * 1. Sends messages to the LLM with available tools
 * 2. Executes tool calls via ToolExecutorManager
 * 3. Streams responses back to the frontend
 * 4. Detects panel changes mid-stream to refresh tool availability
 * 
 * KEY DEPENDENCIES:
 * - PANEL_NAVIGATION_TOOL_MAP: Imported from shared/panel-navigation-map.ts
 *   DO NOT duplicate this map here. See that file for rules on adding panels.
 * 
 * - ToolExecutorManager: Routes tools to correct executor (baseline/panel/mcp)
 * - ToolDiscoveryService: Discovers available tools for current context
 * 
 * - Provider selection is centralized in api-providers/provider-resolver.ts
 *   Do NOT add provider detection logic here.
 * 
 * MODIFICATION RULES:
 * - When adding new panel navigation tools, update shared/panel-navigation-map.ts
 * - When changing tool execution flow, update both this file and messageHandler.ts
 * - System prompt injection happens here via getStoredPrompt()
 * 
 * ============================================================================
 */

import { LoggingService } from '../../../core/logging';
import { ServiceContainerImpl } from '../../../core/service-container';
import { ToolExecutorManager } from '../tool-executor-manager';
import { ToolDiscoveryService } from '../tool-discovery-service';
import { AIProviderFactory } from '../../api-providers/factory';
import { AIMessage, AIFunctionToolDefinition, AIStreamCallbacks, ReasoningConfig } from '../../api-providers/types';
import { getStoredPrompt } from '../../system-prompt/systemPromptBuilder';
import { getPanelContext } from '../../dynamic-panels/code-editor/panel-context';
import { getActiveProvider, getConfiguredModel } from '../../api-providers/provider-resolver';
import { readPromptNotesSync } from '../../system-prompt/recursivePromptNotesService';

import { DEFAULT_AI_TEMPERATURE } from '../../api-providers/utils';
import {
  hasAssistantTextBeforeTools,
  generatePreToolAcknowledgment,
  checkToolCallLimit,
  shouldBlockToolCall
} from './tool-policy';

// CRITICAL: Import from shared source - DO NOT duplicate this map
// See shared/panel-navigation-map.ts for rules on adding new panels
import { PANEL_NAVIGATION_TOOL_MAP, getPanelIdForTool } from '../../../../../../shared/panel-navigation-map';

export type ToolOrigin = 'baseline' | 'panel' | 'mcp';

/** Minimum length to consider a string as base64 image data worth stripping */
const MIN_BASE64_IMAGE_LENGTH = 1000;

/**
 * Sanitize tool result before adding to LLM conversation.
 * Removes large binary data (like base64 screenshots) that would bloat context.
 * The full result is still sent to the frontend via onToolExecution callback.
 */
function sanitizeToolResultForLLM(toolName: string, result: unknown): unknown {
  if (result === null || result === undefined) return result;
  
  // Handle screenshot results - strip the base64 data URL but keep all metadata
  if (toolName === 'capture_screenshot' && typeof result === 'object') {
    const r = result as Record<string, unknown>;
    if (r.url && typeof r.url === 'string' && r.url.startsWith('data:image/')) {
      // Return all metadata, just not the massive base64 string
      return {
        captured: true,
        format: r.format ?? 'png',
        width: r.width,
        height: r.height,
        timestamp: r.timestamp,
        annotationId: r.annotationId ?? null,
        fullPage: r.fullPage ?? false,
        clip: r.clip ?? null,
        showAnnotations: r.showAnnotations ?? true,
        message: 'Screenshot captured successfully. The image is displayed to the user in the chat interface.'
      };
    }
  }
  
  // Handle any result with embedded base64 images
  if (typeof result === 'object') {
    const r = result as Record<string, unknown>;
    // Check for common patterns of embedded images
    for (const key of ['url', 'image', 'data', 'screenshot', 'dataUrl']) {
      const val = r[key];
      if (typeof val === 'string' && val.startsWith('data:image/') && val.length > MIN_BASE64_IMAGE_LENGTH) {
        return {
          ...r,
          [key]: '[base64 image data omitted - displayed to user]',
          _imageCaptured: true
        };
      }
    }
  }
  
  return result;
}

/**
 * Format prompt notes section for environment details.
 * Returns empty string if no notes exist.
 */
function formatPromptNotesSection(panelId: string | null | undefined): string {
  if (!panelId) return '';
  
  const notes = readPromptNotesSync(panelId);
  if (!notes || notes.length === 0) {
    return '';
  }
  
  // Show last 8 notes to avoid bloat
  const MAX_NOTES = 8;
  const recentNotes = notes.slice(-MAX_NOTES);
  const truncatedMessage = notes.length > MAX_NOTES 
    ? `\n(Showing ${MAX_NOTES} of ${notes.length} notes)` 
    : '';
  
  const notesList = recentNotes.map((note, i) => `${i + 1}. ${note}`).join('\n');
  
  return `
# Self-Improvement Notes
These notes were recorded by the AI to improve future interactions:
${notesList}${truncatedMessage}
`;
}

/**
 * Build environment details block for code-editor panel context injection.
 * This follows the Cursor/Cline SOTA pattern of injecting editor state into user messages.
 * 
 * @param panelId - The active panel ID
 * @returns XML-formatted environment details string, or empty string if not code-editor
 */
function buildEnvironmentDetails(panelId: string | null | undefined): string {
  if (panelId !== 'code-editor') {
    return '';
  }

  const context = getPanelContext('code-editor');
  
  if (!context || !context.filePath) {
    return '';
  }

  // Even if content is empty, provide the file path so model knows what to target
  const hasContent = context.content && context.content.trim().length > 0;
  
  // Truncate very large files to avoid context overflow (keep first 50KB)
  const MAX_CONTENT_LENGTH = 50000;
  const truncatedContent = hasContent 
    ? (context.content!.length > MAX_CONTENT_LENGTH
        ? context.content!.substring(0, MAX_CONTENT_LENGTH) + '\n... (truncated, file continues)'
        : context.content!)
    : '// File is empty or content not yet loaded';

  const contentLineCount = hasContent ? context.content!.split('\n').length : 0;

  return `<environment_details>
# Active File
${context.filePath}

# Workspace Root
${context.workspaceRoot}

# Language
${context.language || 'plaintext'}

# File Status
${context.dirty ? 'Unsaved changes' : 'Saved'}
${!hasContent ? '⚠️ Content not loaded - use code_editor_read to get file content' : ''}

# Editor Mode
${context.mode || 'edit'}

# File Content (${contentLineCount} lines)
<file path="${context.filePath}">
${truncatedContent}
</file>
${formatPromptNotesSection(panelId)}
</environment_details>

`;
}

/**
 * Inject environment details into the last user message.
 * This ensures the model always knows the current editor state without needing to call tools.
 * 
 * @param messages - The message array to process
 * @param panelId - The active panel ID
 * @returns Modified messages array with environment details injected
 */
function injectEnvironmentDetailsIntoMessages(
  messages: AIMessage[],
  panelId: string | null | undefined
): AIMessage[] {
  const envDetails = buildEnvironmentDetails(panelId);
  if (!envDetails) {
    return messages;
  }

  // Find the last user message and prepend environment details
  const result = [...messages];
  for (let i = result.length - 1; i >= 0; i--) {
    if (result[i].role === 'user') {
      const originalContent = result[i].content || '';
      result[i] = {
        ...result[i],
        content: envDetails + originalContent,
      };
      break;
    }
  }

  return result;
}

/**
 * Build reasoning config - always enabled by default.
 * OpenRouter's unified API handles model compatibility.
 */
function buildReasoningConfig(model: string): ReasoningConfig | undefined {
  const reasoningEffort = process.env.AI_REASONING_EFFORT as 'minimal' | 'low' | 'medium' | 'high' | undefined;
  const reasoningMaxTokens = process.env.AI_REASONING_MAX_TOKENS ? parseInt(process.env.AI_REASONING_MAX_TOKENS, 10) : undefined;

  // Reasoning disabled only if explicitly set to false
  if (process.env.AI_REASONING_ENABLED === 'false') {
    return undefined;
  }

  return {
    enabled: true,
    effort: reasoningEffort || 'medium',
    maxTokens: reasoningMaxTokens,
    includeReasoning: true,
  };
}

export interface AuralisAgentRunRequest {
  messages: AIMessage[];
  tools?: AIFunctionToolDefinition[];
  toolOrigins?: Record<string, ToolOrigin>;
  maxIterations?: number;
  provider?: 'openrouter' | 'minimax';
  panelId?: string | null;
  userId?: string | null;
}

export interface ExecutedToolCallSummary {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
  result: unknown;
  success: boolean;
  error?: string;
}

export interface AuralisAgentRunResult {
  content: string;
  toolCalls: ExecutedToolCallSummary[];
  model: string;
}

/**
 * Internal agent loop configuration
 */
interface AgentLoopConfig {
  streaming: boolean;
  handlers?: AIStreamCallbacks & { onToolExecution?: (summary: ExecutedToolCallSummary) => void };
}

/**
 * Unified agent loop that handles both streaming and non-streaming execution.
 * This eliminates the ~80% code duplication between runAuralisAgent and runAuralisAgentStream.
 */
async function executeAgentLoop(
  serviceContainer: ServiceContainerImpl,
  logger: LoggingService,
  request: AuralisAgentRunRequest,
  config: AgentLoopConfig
): Promise<AuralisAgentRunResult> {
  const factory = new AIProviderFactory(logger);
  // Use centralized provider resolver - request.provider can override for testing
  const providerName = request.provider || getActiveProvider();
  const provider = factory.createProvider(providerName);

  // Use centralized model resolver - DO NOT access CONTROLS_AGENT_MODEL directly
  const model = getConfiguredModel() || '';
  const defaultMaxIterations = parseInt(process.env.AI_MAX_ITERATIONS || '25', 10);
  const maxIterations = request.maxIterations ?? defaultMaxIterations;
  const executorManager = new ToolExecutorManager(serviceContainer, logger);
  const toolDiscovery = new ToolDiscoveryService(logger, serviceContainer);

  // CRITICAL: Make toolOrigins mutable so we can refresh it mid-stream when panels change
  const mutableToolOrigins: Record<string, ToolOrigin> = { ...(request.toolOrigins || {}) };
  let currentPanelId = request.panelId ?? null;

  let iteration = 0;
  let fullResponse = '';

  // ARCHITECTURE NOTE (SYSTEM PROMPT INJECTION)
  // The refined System Prompt is built by POST /system-prompt/pre-enhanced (builder)
  // followed by POST /system-prompt/enhanced (refiner), stored per user/session.
  // The frontend never sends prompt text. Instead, this orchestrator reads
  // the latest refinedPrompt for the user and injects it as a leading system message.
  const promptSnapshot = getStoredPrompt(request.userId || null);

  if (!promptSnapshot) {
    logger.warn(`[AuralisAgent${config.streaming ? 'Stream' : ''}] No stored system prompt found for user`, {
      userId: request.userId,
      panelId: request.panelId,
    });
  }

  const systemMessages: AIMessage[] = promptSnapshot?.refinedPrompt
    ? [{ role: 'system', content: promptSnapshot.refinedPrompt }]
    : [];

  // SOTA Pattern: Inject environment details into user messages (like Cursor/Cline)
  const messagesWithContext = injectEnvironmentDetailsIntoMessages(request.messages, request.panelId);
  const currentMessages = [...systemMessages, ...messagesWithContext];
  const allExecutedToolCalls: ExecutedToolCallSummary[] = [];

  // Build reasoning config for streaming (only applicable to streaming mode)
  const reasoningConfig = config.streaming ? buildReasoningConfig(model) : undefined;
  if (reasoningConfig?.enabled) {
    logger.info('[AuralisAgentStream] Reasoning enabled', {
      model,
      effort: reasoningConfig.effort,
      maxTokens: reasoningConfig.maxTokens,
    });
  }

  while (iteration < maxIterations) {
    iteration++;

    // Execute LLM call - streaming or non-streaming based on config
    const response = config.streaming && config.handlers
      ? await provider.streamChatCompletion({
          model,
          messages: currentMessages,
          tools: request.tools,
          temperature: parseFloat(process.env.AI_TEMPERATURE || String(DEFAULT_AI_TEMPERATURE)),
          max_tokens: parseInt(process.env.AI_MAX_TOKENS || '4000', 10),
          reasoning: reasoningConfig,
        }, config.handlers)
      : await provider.chatCompletion({
          model,
          messages: currentMessages,
          tools: request.tools,
          temperature: parseFloat(process.env.AI_TEMPERATURE || String(DEFAULT_AI_TEMPERATURE)),
          max_tokens: parseInt(process.env.AI_MAX_TOKENS || '4000', 10)
        });

    if (config.streaming) {
      logger.info('[AuralisAgent] streamChatCompletion completed iteration', {
        iteration,
        contentLength: (response.content || '').length,
        toolCalls: response.tool_calls?.length ?? 0,
        hasReasoning: !!response.reasoning,
        hasReasoningDetails: !!response.reasoning_details?.length
      });
    }

    if (response.content) {
      fullResponse += response.content;
    }

    // Add assistant message to history
    // CRITICAL: Preserve reasoning_details for interleaved thinking per OpenRouter spec
    // https://openrouter.ai/docs/guides/best-practices/reasoning-tokens#preserving-reasoning-blocks
    if (response.tool_calls && response.tool_calls.length > 0) {
      currentMessages.push({
        role: 'assistant',
        content: response.content || '',
        tool_calls: response.tool_calls,
        // MUST preserve reasoning_details - enables model to continue reasoning from where it left off
        reasoning_details: response.reasoning_details
      });
    } else if (response.content) {
      currentMessages.push({ 
        role: 'assistant', 
        content: response.content,
        // Also preserve for non-tool responses (future-proofing)
        reasoning_details: response.reasoning_details
      });
    }

    // Exit if no tool calls
    if (!response.tool_calls || response.tool_calls.length === 0) {
      break;
    }

    // Policy: Pre-tool acknowledgment
    if (!hasAssistantTextBeforeTools(response.content || '')) {
      const ackMessage = generatePreToolAcknowledgment(response.tool_calls.length);
      fullResponse += ackMessage;

      // Update history
      const lastMsg = currentMessages[currentMessages.length - 1];
      if (lastMsg && lastMsg.role === 'assistant' && lastMsg.tool_calls) {
        lastMsg.content = ackMessage;
      }

      // Emit ack chunk for streaming mode
      if (config.streaming && config.handlers?.onToken) {
        config.handlers.onToken(ackMessage);
      }
    }

    // Policy: Tool call limit
    const limitCheck = checkToolCallLimit(allExecutedToolCalls.length + response.tool_calls.length);
    if (!limitCheck.withinLimit) {
      logger.warn(`[AuralisAgent] Tool call limit reached (${limitCheck.remaining} remaining)`);
      break;
    }

    // Execute tools
    for (const tc of response.tool_calls) {
      const toolName = tc.function.name;
      const origin = mutableToolOrigins[toolName] || 'baseline';

      if (config.streaming) {
        logger.info('[AuralisAgentStream] Tool origin lookup', {
          toolName,
          resolvedOrigin: origin,
          hasToolOrigins: Boolean(mutableToolOrigins),
          originInMap: toolName in mutableToolOrigins,
          mapValue: mutableToolOrigins[toolName],
        });
      }

      let args: Record<string, unknown> = {};
      let parseError: string | null = null;

      // Handle empty or missing arguments gracefully
      const rawArgs = tc.function.arguments || '';
      const trimmedArgs = rawArgs.trim();

      // DEBUG: Log raw arguments for code_editor_write to trace encoding issues
      if (toolName === 'code_editor_write' && trimmedArgs.length > 0) {
        const hasEntities = trimmedArgs.includes('&lt;') || trimmedArgs.includes('&gt;');
        logger.info(`[AuralisAgent] RAW TOOL ARGS for ${toolName}`, {
          hasHtmlEntities: hasEntities,
          rawPreview: trimmedArgs.substring(0, 300),
          length: trimmedArgs.length,
        });
      }

      if (trimmedArgs === '' || trimmedArgs === '{}') {
        args = {};
      } else {
        try {
          args = JSON.parse(trimmedArgs);
          
          // AUTO-FIX: Decode HTML entities in content fields (LLMs sometimes double-encode HTML)
          const contentFields = ['contents', 'content', 'newString', 'oldString', 'text', 'html'];
          const codeEditorTools = ['code_editor_write', 'code_editor_replace', 'code_editor_apply_edits'];
          
          if (codeEditorTools.includes(toolName)) {
            for (const field of contentFields) {
              if (args[field] && typeof args[field] === 'string') {
                const value = args[field] as string;
                if (value.includes('&lt;') || value.includes('&gt;') || value.includes('&amp;')) {
                  logger.warn(`[AuralisAgent] DOUBLE-ENCODING DETECTED in ${toolName}.${field}`, {
                    preview: value.substring(0, 150),
                  });
                  // Decode HTML entities that were incorrectly encoded by the LLM
                  args[field] = value
                    .replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>')
                    .replace(/&amp;/g, '&')
                    .replace(/&quot;/g, '"')
                    .replace(/&#39;/g, "'")
                    .replace(/&#x27;/g, "'")
                    .replace(/&#x2F;/g, '/');
                  logger.info(`[AuralisAgent] Auto-decoded HTML entities in ${toolName}.${field}`);
                }
              }
            }
          }
        } catch (e) {
          parseError = `Invalid JSON arguments: ${e instanceof Error ? e.message : String(e)}`;
          logger.warn(`[AuralisAgent] Failed to parse tool arguments for ${toolName}`, {
            error: e,
            rawArgs: rawArgs.substring(0, 100),
            length: rawArgs.length
          });
        }
      }

      // Policy: Blocked tools
      const policyCheck = shouldBlockToolCall(toolName);
      if (policyCheck.blocked) {
        logger.warn(`[AuralisAgent] Tool call blocked: ${toolName} - ${policyCheck.reason}`);
        const result = { success: false, error: `Tool execution blocked by policy: ${policyCheck.reason}`, result: null };

        const summary: ExecutedToolCallSummary = {
          id: tc.id,
          type: 'function',
          function: { name: toolName, arguments: tc.function.arguments },
          result: result.result,
          success: result.success,
          error: result.error
        };
        allExecutedToolCalls.push(summary);
        if (config.streaming && config.handlers?.onToolExecution) {
          config.handlers.onToolExecution(summary);
        }

        currentMessages.push({
          role: 'tool',
          tool_call_id: tc.id,
          name: toolName,
          content: JSON.stringify({ error: result.error })
        });
        continue;
      }

      const result = parseError
        ? { success: false, error: parseError, result: null }
        : await executorManager.executeTool(toolName, origin, args, { panelId: currentPanelId ?? null });

      const summary: ExecutedToolCallSummary = {
        id: tc.id,
        type: 'function',
        function: { name: toolName, arguments: tc.function.arguments },
        result: result.result,
        success: result.success,
        error: result.error
      };

      allExecutedToolCalls.push(summary);
      if (config.streaming && config.handlers?.onToolExecution) {
        config.handlers.onToolExecution(summary);
      }

      // CRITICAL FIX: Refresh tool origins when panel navigation succeeds
      // Uses shared getPanelIdForTool() helper - see shared/panel-navigation-map.ts
      const newPanelId = getPanelIdForTool(toolName);
      if (result.success && newPanelId) {
        logger.info(`[AuralisAgent${config.streaming ? 'Stream' : ''}] Panel navigation detected, refreshing tool origins`, {
          toolName,
          oldPanelId: currentPanelId,
          newPanelId,
        });
        currentPanelId = newPanelId;
        const refreshedTools = toolDiscovery.discoverTools(newPanelId);
        Object.assign(mutableToolOrigins, refreshedTools.tool_origins);
        logger.info(`[AuralisAgent${config.streaming ? 'Stream' : ''}] Tool origins refreshed`, {
          newOriginCount: Object.keys(refreshedTools.tool_origins).length,
          totalOriginCount: Object.keys(mutableToolOrigins).length,
        });
      }

      // Sanitize result before adding to LLM context (removes large base64 data)
      const sanitizedResult = result.success
        ? sanitizeToolResultForLLM(toolName, result.result)
        : { error: result.error };

      currentMessages.push({
        role: 'tool',
        tool_call_id: tc.id,
        name: toolName,
        content: JSON.stringify(sanitizedResult)
      });

      // Check for continue_workflow signal
      if (toolName === 'continue_workflow' && result.success) {
        const continueResult = result.result as { _continue?: boolean; reason?: string; context?: string } | null;
        if (continueResult?._continue) {
          logger.info('[AuralisAgent] continue_workflow detected, will inject continuation prompt', {
            reason: continueResult.reason,
            hasContext: !!continueResult.context,
          });
          // Inject a synthetic user message with full context to keep the model focused
          const continuationParts: string[] = [];
          if (continueResult.reason) {
            continuationParts.push(`Next step: ${continueResult.reason}`);
          }
          if (continueResult.context) {
            continuationParts.push(`\nWorkflow context:\n${continueResult.context}`);
          }
          continuationParts.push('\nProceed with the next action.');
          
          currentMessages.push({
            role: 'user',
            content: continuationParts.join(''),
          });
        }
      }
    }
  }

  return {
    content: fullResponse,
    toolCalls: allExecutedToolCalls,
    model
  };
}

/**
 * Run Auralis agent in non-streaming mode.
 * Use this for simple request-response scenarios.
 */
export async function runAuralisAgent(
  serviceContainer: ServiceContainerImpl,
  logger: LoggingService,
  request: AuralisAgentRunRequest,
): Promise<AuralisAgentRunResult> {
  return executeAgentLoop(serviceContainer, logger, request, { streaming: false });
}

/**
 * Run Auralis agent in streaming mode with real-time callbacks.
 * Use this for chat interfaces that need progressive updates.
 */
export async function runAuralisAgentStream(
  serviceContainer: ServiceContainerImpl,
  logger: LoggingService,
  request: AuralisAgentRunRequest,
  handlers: AIStreamCallbacks & { onToolExecution?: (summary: ExecutedToolCallSummary) => void },
): Promise<AuralisAgentRunResult> {
  return executeAgentLoop(serviceContainer, logger, request, { streaming: true, handlers });
}
