/**
 * Auralis Agent Service - Backend SSE Communication
 * 
 * ARCHITECTURE NOTES:
 * - Modern replacement for legacy OpenRouter direct integration
 * - POSTs chat payloads to `/api/ai/auralis/agent/run`
 * - Consumes Server-Sent Events (SSE) response stream
 * - Routes events: tokens, tool_calls, tool_result, final, error
 * - Aggregates final content + backend-executed tool summaries
 * 
 * DESIGN DECISIONS:
 * - Provider flag is now telemetry/diagnostic only
 * - Backend is single integration point for LLMs and tools
 * - Idle timeout (2min) protects UI from hanging
 * - Development mode bypasses Vite proxy (SSE buffering issues)
 * - Partial results returned on error if content exists
 */

import type { ChatMessage, ToolOrigin, ToolCall } from './types';
import { safeParseJson } from './formatting';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

/**
 * Streaming tool call structure
 * Shape of in-flight tool calls as streamed by backend
 */
export interface StreamingToolCall {
    id: string;
    type: 'function';
    function: {
        name: string;
        arguments: string;
    };
    parsedArguments?: Record<string, unknown>;
}

/**
 * Backend-executed tool call summary
 * Compact summary of completed tool call from backend
 */
export interface BackendExecutedToolCallSummary {
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

/**
 * Final streaming result
 * Aggregate result consumed by messageHandler after streaming completes
 */
export interface AuralisAgentStreamingResult {
    content: string;
    toolCalls: BackendExecutedToolCallSummary[];
    model: string;
    error?: string;
    incomplete?: boolean;
}

// ToolCall is imported from ./types.ts - DO NOT redefine here
// See types.ts for the canonical ToolCall interface

/**
 * Tool execution result structure
 */
export interface ToolExecutionResult {
    success: boolean;
    result: unknown;
    error?: string;
}

/**
 * Union of SSE events from backend
 */
type AgentStreamEvent =
    | { type: 'token'; content: string }
    | { type: 'reasoning'; content: string }
    | { type: 'tool_calls'; toolCalls: StreamingToolCall[] }
    | { type: 'tool_result'; toolCall: BackendExecutedToolCallSummary }
    | { type: 'final'; content: string; toolCalls: BackendExecutedToolCallSummary[]; model: string }
    | { type: 'error'; error: string;[key: string]: unknown }
    | { type: 'debug';[key: string]: unknown }
    | { type: string;[key: string]: unknown };

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Convert backend summary to ToolCall shape used by UI
 */
function mapSummaryToToolCall(summary: BackendExecutedToolCallSummary): ToolCall {
    const argsString = summary.function?.arguments ?? '';
    const parsedArguments = safeParseJson<Record<string, unknown> | null>(argsString, null) ?? undefined;

    return {
        id: summary.id,
        type: summary.type,
        function: {
            name: summary.function?.name || '',
            arguments: argsString,
        },
        parsedArguments,
        result: summary.result,
    } as ToolCall;
}

/**
 * Convert backend summary to ToolExecutionResult
 */
function mapSummaryToExecutionResult(summary: BackendExecutedToolCallSummary): ToolExecutionResult {
    return {
        success: summary.success,
        result: summary.result,
        error: summary.error,
    };
}

// ============================================================================
// MAIN STREAMING FUNCTION
// ============================================================================

/**
 * Send streaming request to Auralis backend agent
 * 
 * FLOW:
 * 1. POST conversation + tools to backend
 * 2. Read SSE stream incrementally
 * 3. Call callbacks for UI updates (tokens, reasoning, tool execution)
 * 4. Return final aggregated result
 * 
 * ERROR HANDLING:
 * - Returns partial result if content exists before error
 * - Throws if no content and error occurs
 * - Idle timeout after 2 minutes of no activity
 * 
 * @param messages - Conversation history
 * @param tools - Available tool definitions
 * @param maxIterations - Max tool execution iterations
 * @param onChunk - Callback for each content token
 * @param toolOrigins - Tool origin classification
 * @param onToolExecution - Callback for tool execution results
 * @param onStreamingToolCalls - Callback for streaming tool calls
 * @param onReasoning - Callback for reasoning/thinking tokens
 * @param panelId - Active panel ID for context
 * @param abortSignal - Optional abort signal to cancel the stream
 */
export async function sendAuralisAgentStreamingRequest(
    messages: ChatMessage[],
    tools: Array<{
        name: string;
        type: 'function';
        function: {
            name: string;
            description?: string;
            parameters?: Record<string, unknown>;
        };
    }> = [],
    maxIterations: number,
    onChunk: (content: string) => void,
    toolOrigins: Record<string, ToolOrigin>,
    onToolExecution: (toolCall: ToolCall, result: ToolExecutionResult) => void,
    onStreamingToolCalls: (toolCalls: StreamingToolCall[]) => void,
    onReasoning?: (chunk: string) => void,
    panelId?: string | null,
    abortSignal?: AbortSignal,
): Promise<AuralisAgentStreamingResult> {
    console.log('[auralisAgentService] Starting SSE stream request');

    // ========================================================================
    // ENDPOINT CONFIGURATION
    // ========================================================================

    // In development, bypass Vite proxy (buffers SSE) and talk directly to gateway
    // In production, respect VITE_API_BASE_URL
    const isDev = import.meta.env.DEV;
    const gatewayPort = import.meta.env.VITE_GATEWAY_PORT || '7085';
    const configuredBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
    const apiBaseUrl = isDev ? `http://localhost:${gatewayPort}` : configuredBase;
    const url = `${apiBaseUrl || ''}/api/ai/auralis/agent/run`;
    console.log('[auralisAgentService] SSE endpoint:', url);

    // ========================================================================
    // REQUEST PAYLOAD
    // ========================================================================

    const requestBody = {
        messages: messages.map((m) => ({ role: m.role, content: m.content })),
        tools,
        toolOrigins,
        maxIterations,
        panelId: panelId ?? null,
    };

    // ========================================================================
    // FETCH REQUEST
    // ========================================================================

    console.log('[auralisAgentService] About to send fetch request...');

    const devUserId = import.meta.env.VITE_DEV_AUTH_USER_ID || 'local-dev-user';
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        // Dev auth header
        ...(devUserId ? { 'x-user-id': devUserId } : {}),
    };

    const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestBody),
        signal: abortSignal,
    });


    console.log('[auralisAgentService] Response received:', response.status, response.statusText);
    console.log('[auralisAgentService] Response headers:', Object.fromEntries(response.headers.entries()));

    if (!response.ok) {
        const errorText = await response.text().catch(() => response.statusText);
        console.warn('[auralisAgentService] Response not OK, returning incomplete result:', response.status, errorText);
        return {
            content: '',
            toolCalls: [],
            model: 'auralis-backend',
            error: `Auralis agent API error: ${response.status} ${errorText}`,
            incomplete: true,
        };
    }

    const body = response.body;
    if (!body) {
        console.warn('[auralisAgentService] Response body not readable, returning empty result');
        return {
            content: '',
            toolCalls: [],
            model: 'auralis-backend',
            error: 'Auralis agent response body is not readable',
            incomplete: true,
        };
    }

    console.log('[auralisAgentService] Response body exists, creating reader...');

    // ========================================================================
    // STREAM PROCESSING
    // ========================================================================

    const reader = body.getReader();
    const decoder = new TextDecoder();

    console.log('[auralisAgentService] Stream reader created successfully');
    console.log('[auralisAgentService] Reader state:', { locked: body.locked });

    // State tracking
    let buffer = '';
    let fullContent = '';
    let finalToolCalls: BackendExecutedToolCallSummary[] = [];
    let finalModel = '';

    let exitReason = 'unknown';

    try {
        let streaming = true;
        while (streaming) {
            console.log('[auralisAgentService] About to read from stream...');
            const { done, value } = await reader.read();
            console.log('[auralisAgentService] Read result:', { done, valueLength: value?.length });

            if (done) {
                console.log('[auralisAgentService] Stream done signal received');
                exitReason = 'reader_done';
                break;
            }

            if (!value) {
                continue;
            }

            // Decode chunk
            try {
                buffer += decoder.decode(value, { stream: true });
            } catch (decodeErr) {
                console.error('[auralisAgentService] TextDecoder error:', decodeErr);
                continue;
            }

            // Process SSE lines
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.trim()) {
                    continue;
                }

                // Skip comment lines (heartbeat)
                if (line.startsWith(':')) {
                    continue;
                }

                // Parse SSE data line
                if (!line.startsWith('data: ')) {
                    continue;
                }

                const data = line.slice(6);
                if (data === '[DONE]') {
                    streaming = false;
                    exitReason = '[DONE]';
                    break;
                }

                let event: AgentStreamEvent;
                try {
                    event = JSON.parse(data) as AgentStreamEvent;
                } catch {
                    continue;
                }

                // Skip debug events
                if (event.type === 'debug') {
                    console.log('[auralisAgentService] Received debug event:', event);
                    continue;
                }

                console.log('[auralisAgentService] Received SSE event:', event.type);

                // Route event to appropriate handler
                switch (event.type) {
                    case 'token': {
                        const chunk = typeof event.content === 'string' ? event.content : '';
                        if (chunk) {
                            console.log('[auralisAgentService] Token chunk received, length:', chunk.length);
                            fullContent += chunk;
                            onChunk(chunk);

                        }
                        break;
                    }
                    case 'reasoning': {
                        const chunk = typeof event.content === 'string' ? event.content : '';
                        if (chunk && onReasoning) {
                            console.log('[auralisAgentService] Reasoning chunk received, length:', chunk.length);
                            onReasoning(chunk);

                        }
                        break;
                    }
                    case 'tool_calls': {
                        const toolCalls = Array.isArray(event.toolCalls) ? (event.toolCalls as StreamingToolCall[]) : [];
                        if (toolCalls.length > 0) {
                            onStreamingToolCalls(toolCalls);

                        }
                        break;
                    }
                    case 'tool_result': {
                        const summary = event.toolCall as BackendExecutedToolCallSummary | undefined;
                        if (summary && summary.function && typeof summary.function.name === 'string') {
                            const toolCall = mapSummaryToToolCall(summary);
                            const execResult = mapSummaryToExecutionResult(summary);
                            onToolExecution(toolCall, execResult);

                        }
                        break;
                    }
                    case 'final': {
                        console.log('[auralisAgentService] Final event received');
                        if (typeof event.content === 'string' && event.content.length > 0) {
                            fullContent = event.content;
                        }
                        if (Array.isArray(event.toolCalls)) {
                            finalToolCalls = event.toolCalls as BackendExecutedToolCallSummary[];
                        }
                        if (typeof event.model === 'string') {
                            finalModel = event.model;
                        }
                        break;
                    }
                    case 'error': {
                        const message = typeof event.error === 'string' ? event.error : 'Auralis agent error';
                        exitReason = `error_event:${message}`;
                        // Capture error but do not throw; return partial result
                        return {
                            content: fullContent,
                            toolCalls: finalToolCalls,
                            model: finalModel || 'auralis-backend',
                            error: message,
                            incomplete: true,
                        };
                    }
                    default:
                        // Ignore unknown event types
                        break;
                }
            }
        }
    } catch (err) {
        console.error('[auralisAgentService] Stream error:', err);
        exitReason = exitReason === 'unknown' ? 'exception' : exitReason;

        const errorMessage = err instanceof Error ? err.message : 'Stream error';
        // Return whatever we have, even if empty
        return {
            content: fullContent,
            toolCalls: finalToolCalls,
            model: finalModel || 'auralis-backend',
            error: errorMessage,
            incomplete: true,
        };
    } finally {
        try {
            reader.releaseLock();
        } catch {
            // ignore
        }
    }

    console.log('[auralisAgentService] Stream completed', {
        reason: exitReason || 'completed',
        contentLength: fullContent.length,
        toolCalls: finalToolCalls.length,
    });

    return {
        content: fullContent,
        toolCalls: finalToolCalls,
        model: finalModel || 'auralis-backend',
    };
}
