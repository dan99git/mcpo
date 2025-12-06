/**
 * Message Handler - Chat Orchestration
 * 
 * ============================================================================
 * ARCHITECTURE NOTES
 * ============================================================================
 * 
 * This is the frontend entry point for sending messages and handling tool execution.
 * It orchestrates: session creation, attachment upload, streaming, tool execution.
 * 
 * KEY DEPENDENCIES:
 * - PANEL_NAVIGATION_TOOL_MAP: Imported from shared/panel-navigation-map.ts
 *   DO NOT duplicate this map here. See that file for rules on adding panels.
 * 
 * - auralisAgentService: Handles SSE streaming to backend orchestrator
 * - useChatStore: React state management for chat UI
 * - eventBus: Publishes UI events (dynamic-panel:open, code-editor:reload)
 * 
 * RESPONSIBILITIES:
 * - Validate and persist user messages
 * - Create/restore chat sessions
 * - Upload attachments before sending
 * - Prepare tool lists and conversation payloads
 * - Drive streaming lifecycle (tokens, tool calls, final messages)
 * - Coordinate tool execution callbacks
 * - Update stream events in real-time
 * - Publish UI events when panel navigation tools succeed
 * 
 * MODIFICATION RULES:
 * - When adding new panel navigation tools, update shared/panel-navigation-map.ts ONLY
 * - Panel navigation event publishing uses getPanelIdForTool() helper
 * - File modification tools trigger 'code-editor:reload' event
 * 
 * ============================================================================
 */

import { STREAM_EVENT_TYPES } from './constants';
import type { ChatMessage, Attachment, StreamEvent, ChatSession } from './types';
import type { PanelContext } from './chat-store';
import { useChatStore } from './chat-store';
import { clearWorkflowStatus } from './workflow-state';
import type { StreamingToolCall, ToolExecutionResult } from './agent-service';
import { sendAuralisAgentStreamingRequest } from './agent-service';
import {
    addSpanEvent,
    endSpan,
    recordModelLatency,
    startSpan,
} from '../tools/tooling';
import { publish } from '../../utils/eventBus';

// CRITICAL: Import from shared source - DO NOT duplicate this map
// See shared/panel-navigation-map.ts for rules on adding new panels
import { getPanelIdForTool } from '../../../../../shared/panel-navigation-map';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

type ToolOrigin = 'baseline' | 'panel' | 'mcp';

interface UploadedAttachment extends Attachment {
    [key: string]: unknown;
}

interface AttachmentWithFile extends Attachment {
    file?: File;
}

interface ToolFunctionShape {
    name?: string;
    description?: string;
    parameters?: Record<string, unknown> | null;
}

interface ToolSummary {
    function?: ToolFunctionShape;
    category?: string;
}

interface ToolsResponsePayload {
    tools: ToolSummary[];
    tool_origins: Record<string, ToolOrigin>;
}

type ChatApi = {
    createSession: (title: string, summary?: string) => Promise<ChatSession>;
    uploadAttachment: (file: File) => Promise<{ file: unknown }>;
    addMessage: (sessionId: string, message: ChatMessage) => Promise<void>;
    getTools: (panelId?: string | null) => Promise<ToolsResponsePayload>;
};

export interface MessageHandlerDeps {
    chatApi: ChatApi;
    getStoreState: () => ReturnType<typeof useChatStore.getState>;
    setError: (message: string | null) => void;
    createSession: (session: ChatSession) => void;
    addMessage: (message: ChatMessage) => void;
    addStreamEvent: (event: StreamEvent) => void;
    updateStreamEvent: (id: string, updates: Partial<StreamEvent>) => void;
    setActiveStreamingEvent: (id: string | null) => void;
    setStreaming: (streaming: boolean) => void;
    setStreamingAbortController: (controller: AbortController | null) => void;
    setPanelContext: (updates: Partial<PanelContext>) => void;
    speak: (text: string) => Promise<void>;
    ensurePanelPrompt?: () => Promise<void>;
}

// ============================================================================
// MAIN HANDLER FACTORY
// ============================================================================

/**
 * Creates a message handler for the Auralis AI chat panel
 * 
 * FLOW:
 * 1. Validate input
 * 2. Create/restore session
 * 3. Upload attachments
 * 4. Add user message to store and backend
 * 5. Fetch tools for current panel
 * 6. Start streaming from backend
 * 7. Handle stream events (tokens, tool calls, thinking)
 * 8. Add final assistant message
 * 9. Trigger TTS if enabled
 * 
 * ERROR HANDLING:
 * - Session creation failure: show error, stop
 * - Attachment upload failure: warn, continue without attachments
 * - Tool loading failure: warn, continue with baseline tools
 * - Streaming failure: show error message, record telemetry
 */
export function createMessageHandler(deps: MessageHandlerDeps) {
    return async function handleSendMessage(content: string, attachments?: AttachmentWithFile[], mode?: 'ask' | 'agent') {
        const trimmedContent = content?.trim();
        if (!trimmedContent) {
            return;
        }

        deps.setError(null);

        // Start telemetry span
        const rootSpan = startSpan('chat_message', undefined, {
            attachmentCount: attachments?.length || 0,
            contentLength: trimmedContent.length,
            mode: mode || 'ask',
        });
        let rootSpanEnded = false;
        const finishRootSpan = (status: 'ok' | 'error', errorMessage?: string) => {
            if (!rootSpanEnded) {
                endSpan(rootSpan.spanId, status, errorMessage);
                rootSpanEnded = true;
            }
        };

        let sessionId = deps.getStoreState().currentSessionId;

        // ========================================================================
        // SESSION HANDLING
        // ========================================================================
        // Reuse existing session or create new one
        if (!sessionId) {
            try {
                const newSession = await deps.chatApi.createSession('New conversation');
                deps.createSession(newSession);
                sessionId = deps.getStoreState().currentSessionId;
                if (!sessionId) {
                    deps.setError('Failed to create session. Please refresh the page.');
                    finishRootSpan('error', 'session_creation_failed');
                    return;
                }
                // Notify saved chats panel
                publish('chat-sessions:updated', { sessionId, reason: 'session-created' });
                // New session: open home panel
                publish('dynamic-panel:open', { panelId: 'home' });
            } catch (err) {
                deps.setError('Failed to create session. Please refresh the page.');
                addSpanEvent(rootSpan.spanId, 'session_creation_failed', {
                    reason: err instanceof Error ? err.message : String(err),
                });
                finishRootSpan('error', 'session_creation_failed');
                return;
            }
        }

        addSpanEvent(rootSpan.spanId, 'session_resolved', {
            sessionId,
        });

        // ========================================================================
        // ATTACHMENT UPLOAD
        // ========================================================================
        // Upload files first so backend references durable file records
        let uploadedAttachments: Attachment[] = [];
        if (attachments && attachments.length > 0) {
            try {
                const uploadPromises = attachments
                    .filter((att) => att?.file)
                    .map(async (att) => {
                        const result = await deps.chatApi.uploadAttachment(att.file!);
                        const fileRecord = (result?.file || {}) as Record<string, unknown>;
                        const fallbackName = att.filename || att.file?.name || 'uploaded-file';
                        const fallbackType = att.mimetype || att.file?.type || 'application/octet-stream';

                        const uploaded: UploadedAttachment = {
                            filename: (fileRecord.filename as string) ?? fallbackName,
                            mimetype: (fileRecord.mimetype as string) ?? fallbackType,
                            originalName: (fileRecord.originalName as string) ?? fallbackName,
                            size: att.file?.size || 0,
                            ...fileRecord,
                        } as UploadedAttachment;

                        if (typeof uploaded.size !== 'number') {
                            uploaded.size = att.file?.size || 0;
                        }
                        return uploaded;
                    });

                uploadedAttachments = await Promise.all(uploadPromises);
                addSpanEvent(rootSpan.spanId, 'attachments_uploaded', {
                    count: uploadedAttachments.length,
                });
            } catch (err) {
                console.warn('[messageHandler] Attachment upload failed (non-critical):', err);
                addSpanEvent(rootSpan.spanId, 'attachment_upload_failed', {
                    reason: err instanceof Error ? err.message : String(err),
                });
            }
        }

        // ========================================================================
        // USER MESSAGE
        // ========================================================================
        // Add user message to store and persist to backend
        const userMessage: ChatMessage = {
            role: 'user',
            content: trimmedContent,
            timestamp: new Date().toISOString(),
            ...(uploadedAttachments.length > 0 ? { attachments: uploadedAttachments } : {}),
        };

        deps.addMessage(userMessage);

        const userEventId = `user-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
        deps.addStreamEvent({
            id: userEventId,
            type: STREAM_EVENT_TYPES.MESSAGE,
            createdAt: Date.now(),
            timestamp: userMessage.timestamp || new Date().toISOString(),
            message: userMessage,
            streaming: false,
        });

        try {
            await deps.chatApi.addMessage(sessionId, userMessage);
            publish('chat-sessions:updated', { sessionId, reason: 'message-added' });
        } catch (err) {
            console.warn('[messageHandler] Failed to persist user message (non-critical):', err);
            addSpanEvent(rootSpan.spanId, 'persist_user_message_failed', {
                reason: err instanceof Error ? err.message : String(err),
            });
        }

        // ========================================================================
        // TOOL LOADING
        // ========================================================================
        // Fetch tools for current panel from backend
        const openPanelId = deps.getStoreState().openPanelId;
        console.log('[messageHandler] Current openPanelId from store:', openPanelId);
        let toolsPayload: ToolsResponsePayload | null = null;
        try {
            toolsPayload = await deps.chatApi.getTools(openPanelId);
            addSpanEvent(rootSpan.spanId, 'tools_loaded', {
                count: toolsPayload?.tools?.length || 0,
                panelId: openPanelId,
            });
        } catch (err) {
            console.warn('[messageHandler] Tools loading failed (non-critical):', err);
            addSpanEvent(rootSpan.spanId, 'tools_load_failed', {
                reason: err instanceof Error ? err.message : String(err),
            });
        }

        const tools = toolsPayload?.tools || [];
        const toolOrigins = toolsPayload?.tool_origins || {};

        // DEBUG: Log the tool origins we received from the API
        const panelToolCount = Object.values(toolOrigins).filter(o => o === 'panel').length;
        const baselineToolCount = Object.values(toolOrigins).filter(o => o === 'baseline').length;
        const mcpToolCount = Object.values(toolOrigins).filter(o => o === 'mcp').length;
        console.log('[messageHandler] Tool origins received:', {
            totalOrigins: Object.keys(toolOrigins).length,
            panelTools: panelToolCount,
            baselineTools: baselineToolCount,
            mcpTools: mcpToolCount,
            samplePanelTools: Object.entries(toolOrigins)
                .filter(([_, o]) => o === 'panel')
                .slice(0, 5)
                .map(([name]) => name),
        });

        // ========================================================================
        // CONVERSATION PREPARATION
        // ========================================================================
        // Build conversation history for backend
        const conversationMessages = deps.getStoreState().messages.map((msg: ChatMessage) => ({
            role: msg.role,
            content: msg.content || '',
            ...(msg.attachments && msg.attachments.length > 0 ? { attachments: msg.attachments } : {}),
        }));

        // ========================================================================
        // STREAMING SETUP
        // ========================================================================
        // Create assistant message placeholder
        let currentTextEventId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
        const assistantMessage: ChatMessage = {
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
        };

        deps.addStreamEvent({
            id: currentTextEventId,
            type: STREAM_EVENT_TYPES.MESSAGE,
            createdAt: Date.now(),
            timestamp: assistantMessage.timestamp || new Date().toISOString(),
            message: assistantMessage,
            streaming: true,
        });

        deps.setActiveStreamingEvent(currentTextEventId);
        deps.setStreaming(true);
        const abortController = new AbortController();
        deps.setStreamingAbortController(abortController);

        const streamStartTime = Date.now();
        let accumulatedContent = '';
        let currentSegmentContent = ''; // Content for the current text segment only
        let lastUpdateTime = Date.now();
        const UPDATE_THROTTLE_MS = 50;
        
        // Track if we need a new text bubble after thinking/tool events
        let needsNewTextBubble = false;

        // Helper to create a new text bubble when resuming after thinking/tools
        const createNewTextBubble = () => {
            currentTextEventId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
            currentSegmentContent = '';
            deps.addStreamEvent({
                id: currentTextEventId,
                type: STREAM_EVENT_TYPES.MESSAGE,
                createdAt: Date.now(),
                timestamp: new Date().toISOString(),
                message: {
                    role: 'assistant',
                    content: '',
                    timestamp: new Date().toISOString(),
                },
                streaming: true,
            });
            deps.setActiveStreamingEvent(currentTextEventId);
            needsNewTextBubble = false;
        };

        // ========================================================================
        // STREAMING CALLBACKS
        // ========================================================================

        /**
         * Handle streaming text chunks
         * Creates new text bubbles when content resumes after thinking/tool events
         */
        const onStreamChunk = (content: string) => {
            // If we need a new bubble (after thinking/tool), create one first
            if (needsNewTextBubble && content.trim()) {
                createNewTextBubble();
            }
            
            accumulatedContent += content;
            currentSegmentContent += content;
            const now = Date.now();
            if (now - lastUpdateTime >= UPDATE_THROTTLE_MS) {
                deps.updateStreamEvent(currentTextEventId, {
                    message: {
                        ...assistantMessage,
                        content: currentSegmentContent,
                    },
                });
                lastUpdateTime = now;
            }
        };

        /**
         * Handle streaming tool calls
         * Creates activity blocks immediately
         * CRITICAL: Dedupe by tool call ID to avoid duplicate blocks from streaming deltas
         * Sets needsNewTextBubble so text after tools goes to a new bubble
         * CRITICAL: Reset thinking state so reasoning after tool calls creates NEW thinking bubbles
         */
        const seenToolCallIds = new Set<string>();
        const onStreamingToolCalls = (toolCalls: StreamingToolCall[]) => {
            toolCalls.forEach((tc) => {
                // Skip if we've already created an activity block for this tool call
                if (tc.id && seenToolCallIds.has(tc.id)) {
                    return;
                }
                if (tc.id) {
                    seenToolCallIds.add(tc.id);
                }

                // Mark that any subsequent text should go to a new bubble
                needsNewTextBubble = true;
                
                // CRITICAL: Reset thinking state so reasoning after this tool call
                // creates a NEW thinking bubble instead of appending to the old one.
                // This enables proper interleaved display: thinking → tool → thinking → tool
                if (thinkingEventId) {
                    // Mark current thinking event as completed
                    deps.updateStreamEvent(thinkingEventId, {
                        streaming: false,
                        status: 'completed',
                        completedAt: Date.now(),
                    });
                }
                thinkingEventId = null;
                accumulatedThinking = '';

                const activityId = `activity-${tc.id || Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
                deps.addStreamEvent({
                    id: activityId,
                    type: STREAM_EVENT_TYPES.ACTIVITY,
                    createdAt: Date.now(),
                    timestamp: new Date().toISOString(),
                    toolCall: {
                        id: tc.id,
                        type: tc.type,
                        function: tc.function,
                    },
                    activity: {
                        tool: tc.function?.name || 'tool',
                        status: 'running',
                    },
                    streaming: true,
                });
            });
        };

        /**
         * Handle reasoning/thinking token stream
         * Creates or updates a thinking stream event to show AI's chain-of-thought
         * Sets needsNewTextBubble so text after thinking goes to a new bubble
         */
        let thinkingEventId: string | null = null;
        let accumulatedThinking = '';
        const onReasoning = (chunk: string) => {
            accumulatedThinking += chunk;

            if (!thinkingEventId) {
                // Mark that any subsequent text should go to a new bubble
                needsNewTextBubble = true;
                
                // Create thinking event on first chunk
                thinkingEventId = `thinking-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
                deps.addStreamEvent({
                    id: thinkingEventId,
                    type: STREAM_EVENT_TYPES.THOUGHT,
                    createdAt: Date.now(),
                    timestamp: new Date().toISOString(),
                    message: {
                        role: 'assistant',
                        content: accumulatedThinking,
                        timestamp: new Date().toISOString(),
                    },
                    streaming: true,
                });
            } else {
                // Update existing thinking event
                deps.updateStreamEvent(thinkingEventId, {
                    message: {
                        role: 'assistant',
                        content: accumulatedThinking,
                        timestamp: new Date().toISOString(),
                    },
                    streaming: true,
                });
            }
        };

        /**
         * Handle tool execution completion
         * Updates activity block with result
         * 
         * CRITICAL: When a panel navigation tool succeeds, we must publish the
         * dynamic-panel:open event so the frontend UI actually changes panels.
         * The backend orchestrator updates activePanelState but cannot notify
         * the frontend - that's our job here.
         */
        const onToolExecution = async (toolCall: any, result: ToolExecutionResult) => {
            // CRITICAL: Look in streamEvents, not messages. Messages don't have a streamEvents property.
            const matchingEvent = deps.getStoreState().streamEvents
                .find((e: any) => e.toolCall?.id === toolCall.id);

            if (matchingEvent) {
                deps.updateStreamEvent(matchingEvent.id, {
                    toolCall: {
                        ...toolCall,
                        result: result.result,
                        success: result.success,
                        error: result.error,
                    },
                    activity: {
                        tool: toolCall.function?.name || 'tool',
                        status: result.success ? 'success' : 'error',
                        result: result.result,
                        error: result.error,
                    },
                    streaming: false,
                });
            }

            // CRITICAL FIX: Handle panel navigation tools executed by backend orchestrator.
            // When the backend runs open_*_panel or close_panel, it updates backend state
            // but the frontend UI won't change unless we publish the event here.
            // Uses shared getPanelIdForTool() - see shared/panel-navigation-map.ts
            if (result.success) {
                const toolName = toolCall.function?.name || '';

                // Use shared helper instead of duplicating the map
                const panelId = getPanelIdForTool(toolName);

                if (panelId) {
                    console.log('[messageHandler] Panel navigation tool succeeded, publishing dynamic-panel:open', { toolName, panelId });
                    publish('dynamic-panel:open', { panelId });

                    // OPTION B: In-stream refresh
                    // Refetch tools for the new panel and update the mutable toolOrigins map
                    // so subsequent tool calls in this same stream are routed correctly.
                    try {
                        console.log(`[messageHandler] Refreshing tools for new panel: ${panelId}`);
                        const newToolsPayload = await deps.chatApi.getTools(panelId);
                        const newOrigins = newToolsPayload.tool_origins || {};

                        // Mutate the existing toolOrigins object
                        Object.assign(toolOrigins, newOrigins);

                        console.log('[messageHandler] Refreshed tool origins:', {
                            count: Object.keys(toolOrigins).length,
                            newOriginsCount: Object.keys(newOrigins).length
                        });
                    } catch (err) {
                        console.warn('[messageHandler] Failed to refresh tools mid-stream:', err);
                    }
                } else if (toolName === 'close_panel') {
                    console.log('[messageHandler] Panel close tool succeeded, publishing dynamic-panel:close');
                    publish('dynamic-panel:close', { panelId: null });
                }

                // CRITICAL: Trigger editor reload when file modification tools succeed
                // This ensures the Monaco editor reflects AI-made changes immediately
                const fileModificationTools = [
                    'code_editor_write',
                    'code_editor_replace',
                    'code_editor_apply_edits',
                    'code_editor_save_as',
                ];
                if (fileModificationTools.includes(toolName)) {
                    console.log('[messageHandler] File modification tool succeeded, publishing code-editor:reload', { toolName });
                    publish('code-editor:reload', { toolName, result: result.result });
                }
            }
        };

        // ========================================================================
        // STREAMING EXECUTION
        // ========================================================================
        try {
            // DEBUG: Log what we're about to send to the backend
            console.log('[messageHandler] About to send to agent:', {
                openPanelId,
                toolCount: tools.length,
                toolOriginsCount: Object.keys(toolOrigins).length,
                panelToolCount: Object.values(toolOrigins).filter(o => o === 'panel').length,
            });

            const result = await sendAuralisAgentStreamingRequest(
                conversationMessages,
                tools.map((t: any) => ({
                    name: t.function?.name || t.name || '',
                    type: 'function' as const,
                    function: {
                        name: t.function?.name || t.name || '',
                        description: t.function?.description || t.description,
                        parameters: t.function?.parameters || t.parameters,
                    },
                })),
                10, // maxIterations
                onStreamChunk,
                toolOrigins,
                onToolExecution,
                onStreamingToolCalls,
                onReasoning,
                openPanelId,
                abortController.signal
            );

            const streamDuration = Date.now() - streamStartTime;
            recordModelLatency(streamDuration);

            // Finalize thinking event if we received reasoning tokens
            if (thinkingEventId && accumulatedThinking) {
                deps.updateStreamEvent(thinkingEventId, {
                    message: {
                        role: 'assistant',
                        content: accumulatedThinking,
                        timestamp: new Date().toISOString(),
                    },
                    streaming: false,
                });
            }

            // Final update with complete content
            assistantMessage.content = result.content || accumulatedContent;
            if (result.toolCalls && result.toolCalls.length > 0) {
                assistantMessage.toolCalls = result.toolCalls.map((tc: any) => ({
                    id: tc.id,
                    type: 'function' as const,
                    function: {
                        name: tc.function?.name || '',
                        arguments:
                            typeof tc.function?.arguments === 'string'
                                ? tc.function.arguments
                                : JSON.stringify(tc.function?.arguments || {}),
                    },
                    result: tc.result,
                }));
            }

            deps.updateStreamEvent(currentTextEventId, {
                message: { ...assistantMessage, content: currentSegmentContent },
                streaming: false,
            });

            deps.addMessage(assistantMessage);

            // TTS playback
            try {
                if (deps.getStoreState().ttsEnabled) {
                    await deps.speak(result.content || '');
                }
            } catch (ttsErr) {
                console.warn('[messageHandler] TTS playback failed (non-critical):', ttsErr);
            }

            // Persist assistant message
            const finalSessionId = deps.getStoreState().currentSessionId;
            if (finalSessionId) {
                try {
                    await deps.chatApi.addMessage(finalSessionId, assistantMessage);
                    publish('chat-sessions:updated', { sessionId: finalSessionId, reason: 'message-added' });
                } catch (err) {
                    console.warn('[messageHandler] Failed to persist assistant message (non-critical):', err);
                    addSpanEvent(rootSpan.spanId, 'persist_assistant_message_failed', {
                        reason: err instanceof Error ? err.message : String(err),
                    });
                }
            }
            finishRootSpan('ok');
        } catch (err) {
            // Streaming failure handling
            const error = err instanceof Error ? err : new Error(String(err));
            const isAbort = (error as any)?.name === 'AbortError';
            if (isAbort) {
                const partialContent = currentSegmentContent || '[Response cancelled]';
                deps.updateStreamEvent(currentTextEventId, {
                    message: {
                        role: 'assistant',
                        content: partialContent,
                        timestamp: new Date().toISOString(),
                    },
                    streaming: false,
                });
                finishRootSpan('error', 'stream_cancelled');
            } else {
                console.error('[messageHandler] Streaming error:', err);
                // Relaxed for debug: Don't show error toast, just log
                // deps.setError(error.message || 'Failed to get AI response');

                // Preserve partial content + error
                const partialContent = currentSegmentContent ? `${currentSegmentContent}\n\n[Stream Error: ${error.message}]` : `Error: ${error.message}`;

                deps.updateStreamEvent(currentTextEventId, {
                    message: {
                        role: 'assistant',
                        content: partialContent,
                        timestamp: new Date().toISOString(),
                    },
                    streaming: false,
                });
                finishRootSpan('error', error.message || 'streaming_error');
            }
        } finally {
            clearWorkflowStatus();
            deps.setActiveStreamingEvent(null);
            deps.setStreaming(false);
            deps.setStreamingAbortController(null);
        }
    };
}
