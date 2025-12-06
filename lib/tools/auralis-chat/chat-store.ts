/**
 * Chat Store - Zustand State Management
 * 
 * ARCHITECTURE NOTES:
 * - Single source of truth for all chat-related state
 * - Uses Zustand with devtools for debugging
 * - openPanelId is CRITICAL - must always match the actual displayed panel
 * 
 * STATE MANAGEMENT RULES:
 * 1. openPanelId MUST reflect the actual panel that is open
 * 2. It is set ONLY by dynamic-panel:open events via setOpenPanelId()
 * 3. All components read from this value - NEVER maintain separate panel state
 * 4. Backend endpoints receive this value to fetch correct tools/prompts
 * 
 * ANTI-PATTERNS TO AVOID:
 * - DO NOT add fallback logic that assumes a default panel
 * - DO NOT use heuristics to "guess" the active panel
 * - DO NOT allow openPanelId to be null or undefined
 * - DO NOT create separate panel tracking in other stores
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { publish } from '@/utils/eventBus';
import type { ChatMessage, Attachment, StreamEvent, ChatSession, ToolOrigin, ToolCounts } from './types';
import { STREAM_EVENT_TYPES } from './constants';
import { chatApi } from '../api';

// ============================================================================
// INTERNAL TYPES
// ============================================================================

/**
 * Tool definition structure
 * Matches backend tool catalog format
 */
interface ToolDefinition {
    function?: {
        name?: string;
        description?: string;
        parameters?: unknown;
    };
    [key: string]: unknown;
}

/**
 * Panel context structure
 * Stores panel-specific state and tool information
 */
export interface PanelContext {
    panelId: string | null;
    panelPrompt: string | null;
    panelContext: Record<string, unknown> | null;
    hasActiveTools: boolean;
    toolDefinitions: Record<string, ToolDefinition>;
    toolOrigins: Record<string, ToolOrigin>;
    visibleControls?: Array<{
        selector: string;
        label: string;
        type: string;
        actionId?: string;
        enabled?: boolean;
        value?: unknown;
    }>;
    surroundingText?: string[];
    surfaceType?: 'html' | 'image' | 'pdf';
}

// ============================================================================
// STORE STATE INTERFACE
// ============================================================================

interface ChatStoreState {
    // Chat history
    messages: ChatMessage[];
    currentSessionId: string | null;
    sessions: ChatSession[];

    /**
     * CRITICAL: SINGLE SOURCE OF TRUTH for active panel
     * This value MUST match the panel actually displayed to the user
     * Used by: tool discovery, system prompt building, panel context
     * Updated by: setOpenPanelId() called from dynamic-panel:open events
     */
    openPanelId: string;

    // Streaming state
    streamEvents: StreamEvent[];
    activeStreamingEventId: string | null;
    activeThoughtEventId: string | null;
    isStreaming: boolean;
    streamingAbortController: AbortController | null;

    // Panel context
    panelContext: PanelContext;

    // Tool state
    toolOrigins: Record<string, ToolOrigin>;
    toolCounts: ToolCounts;

    // Attachment state
    pendingAttachment: Attachment | null;
    attachmentUploadInProgress: boolean;

    // TTS state
    ttsEnabled: boolean;

    // Loading and error state
    isLoading: boolean;
    error: string | null;

    /**
     * Message handler function
     * Registered by AiChatPanel during initialization
     * Called by sendMessage to process user input
     */
    messageHandler: ((content: string, attachments?: Attachment[], mode?: 'ask' | 'agent') => Promise<void>) | null;
}

// ============================================================================
// STORE ACTIONS INTERFACE
// ============================================================================

interface ChatStore extends ChatStoreState {
    // Message actions
    addMessage: (message: ChatMessage) => void;
    clearMessages: () => void;
    loadMessages: (messages: ChatMessage[]) => void;
    sendMessage: (content: string, attachments?: Attachment[], mode?: 'ask' | 'agent') => Promise<void>;
    registerMessageHandler: (
        handler: (content: string, attachments?: Attachment[], mode?: 'ask' | 'agent') => Promise<void>
    ) => void;
    clearMessageHandler: () => void;

    // Session actions
    setCurrentSession: (sessionId: string | null) => void;
    createSession: (session: ChatSession) => void;
    loadSessions: () => Promise<void>;

    // Streaming actions
    addStreamEvent: (event: StreamEvent) => void;
    updateStreamEvent: (id: string, updates: Partial<StreamEvent>) => void;
    removeStreamEvent: (id: string) => void;
    setActiveStreamingEvent: (id: string | null) => void;
    setActiveThoughtEvent: (id: string | null) => void;
    setStreaming: (streaming: boolean) => void;
    setStreamingAbortController: (controller: AbortController | null) => void;
    cancelStreaming: () => void;
    resetStream: (messages?: ChatMessage[]) => void;

    // Panel context actions
    setPanelContext: (updates: Partial<PanelContext>) => void;
    clearPanelContext: () => void;
    setOpenPanelId: (panelId: string) => void;

    // Tool actions
    updateToolOrigins: (origins: Record<string, ToolOrigin>) => void;
    loadAvailableTools: () => Promise<void>;

    // Attachment actions
    setPendingAttachment: (attachment: Attachment | null) => void;
    uploadAttachment: (file: File) => Promise<Attachment | null>;

    // TTS actions
    setTtsEnabled: (enabled: boolean) => void;

    // Loading and error actions
    setLoading: (loading: boolean) => void;
    setError: (error: string | null) => void;
}

// ============================================================================
// INITIAL STATE
// ============================================================================

const initialPanelContext: PanelContext = {
    panelId: null,
    panelPrompt: null,
    panelContext: null,
    hasActiveTools: false,
    toolDefinitions: {},
    toolOrigins: {},
};

const initialState: ChatStoreState = {
    messages: [],
    currentSessionId: null,
    sessions: [],
    openPanelId: 'home', // Default panel on app start - must match actual opened panel
    streamEvents: [],
    activeStreamingEventId: null,
    activeThoughtEventId: null,
    isStreaming: false,
    streamingAbortController: null,
    panelContext: initialPanelContext,
    toolOrigins: {},
    toolCounts: {
        baseline: 0,
        panel: 0,
        mcp: 0,
        total: 0,
    },
    pendingAttachment: null,
    attachmentUploadInProgress: false,
    ttsEnabled: false, // Will be loaded from DB on app init
    isLoading: false,
    error: null,
    messageHandler: null,
};

// ============================================================================
// STORE IMPLEMENTATION
// ============================================================================

export const useChatStore = create<ChatStore>()(
    devtools(
        (set, get) => ({
            ...initialState,

            // ========================================================================
            // MESSAGE ACTIONS
            // ========================================================================

            addMessage: (message) => {
                set((state) => ({
                    messages: [...state.messages, message],
                }));
            },

            clearMessages: () => {
                set({
                    messages: [],
                    streamEvents: [],
                    activeThoughtEventId: null,
                    activeStreamingEventId: null,
                    isStreaming: false
                });
            },

            loadMessages: (messages) => {
                // Create stream events from historical messages
                const events: StreamEvent[] = messages.map((msg) => ({
                    id: `history-${msg.timestamp || Date.now()}-${Math.random().toString(16).slice(2)}`,
                    type: STREAM_EVENT_TYPES.MESSAGE,
                    createdAt: new Date(msg.timestamp || Date.now()).getTime(),
                    timestamp: msg.timestamp || new Date().toISOString(),
                    message: msg,
                    streaming: false,
                }));
                set({ messages, streamEvents: events, activeThoughtEventId: null });
            },

            sendMessage: async (content: string, attachments?: Attachment[], mode?: 'ask' | 'agent') => {
                const handler = get().messageHandler;
                if (!handler) {
                    throw new Error('No message handler registered. Initialize the chat panel before sending messages.');
                }
                await handler(content, attachments, mode);
            },

            registerMessageHandler: (handler) => {
                set({ messageHandler: handler });
            },

            clearMessageHandler: () => {
                set({ messageHandler: null });
            },

            // ========================================================================
            // SESSION ACTIONS
            // ========================================================================

            setCurrentSession: (sessionId) => {
                set({ currentSessionId: sessionId });
            },

            createSession: (session) => {
                set((state) => ({
                    sessions: [...state.sessions, session],
                    currentSessionId: session.id,
                }));
            },

            loadSessions: async () => {
                try {
                    const sessions = await chatApi.getSessions();
                    set({ sessions });
                } catch (error) {
                    console.error('[chatStore] Failed to load sessions:', error);
                }
            },

            // ========================================================================
            // STREAMING ACTIONS
            // ========================================================================

            addStreamEvent: (event) => {
                console.log('[chatStore] addStreamEvent called, event type:', event.type, 'id:', event.id);
                set((state) => ({
                    streamEvents: [...state.streamEvents, event],
                }));
            },

            updateStreamEvent: (id, updates) => {
                console.log('[chatStore] updateStreamEvent called for id:', id, 'updates:', Object.keys(updates));
                set((state) => ({
                    streamEvents: state.streamEvents.map((e) =>
                        e.id === id ? { ...e, ...updates } : e
                    ),
                }));
            },

            removeStreamEvent: (id) => {
                set((state) => ({
                    streamEvents: state.streamEvents.filter((e) => e.id !== id),
                }));
            },

            setActiveStreamingEvent: (id) => {
                set({ activeStreamingEventId: id });
            },

            setActiveThoughtEvent: (id) => {
                set({ activeThoughtEventId: id });
            },

            setStreaming: (streaming) => {
                set({ isStreaming: streaming });
            },

            setStreamingAbortController: (controller) => {
                set({ streamingAbortController: controller });
            },

            cancelStreaming: () => {
                const controller = get().streamingAbortController;
                if (controller) {
                    controller.abort();
                }
                set({
                    isStreaming: false,
                    activeStreamingEventId: null,
                });
            },

            resetStream: (messages = []) => {
                const events: StreamEvent[] = messages.map((msg) => ({
                    id: `history-${msg.timestamp || Date.now()}-${Math.random().toString(16).slice(2)}`,
                    type: STREAM_EVENT_TYPES.MESSAGE,
                    createdAt: new Date(msg.timestamp || Date.now()).getTime(),
                    timestamp: msg.timestamp || new Date().toISOString(),
                    message: msg,
                    streaming: false,
                }));
                set({
                    streamEvents: events,
                    activeStreamingEventId: null,
                    activeThoughtEventId: null,
                    isStreaming: false,
                });
            },

            // ========================================================================
            // PANEL CONTEXT ACTIONS
            // ========================================================================

            setPanelContext: (updates) => {
                set((state) => ({
                    panelContext: { ...state.panelContext, ...updates },
                }));
            },

            clearPanelContext: () => {
                set({ panelContext: initialPanelContext });
            },

            setOpenPanelId: (panelId) => {
                set({ openPanelId: panelId });
            },

            // ========================================================================
            // TOOL ACTIONS
            // ========================================================================

            updateToolOrigins: (origins) => {
                set((state) => {
                    const toolOrigins = { ...origins };
                    const counts: ToolCounts = {
                        baseline: 0,
                        panel: 0,
                        mcp: 0,
                        total: Object.keys(toolOrigins).length,
                    };

                    Object.values(toolOrigins).forEach((origin) => {
                        if (origin === 'baseline') counts.baseline++;
                        else if (origin === 'panel') counts.panel++;
                        else if (origin === 'mcp') counts.mcp++;
                    });

                    // Emit a snapshot for the Tool Inspector debugging UI.
                    if (typeof window !== 'undefined') {
                        const snapshot = {
                            panelId: state.openPanelId || null,
                            timestamp: new Date().toISOString(),
                            tools: Object.keys(toolOrigins).map((name) => ({
                                name,
                                origin: toolOrigins[name],
                            })),
                        } as any;

                        (window as any).__AURALIS_TOOL_INSPECTOR__ = snapshot;
                        try {
                            publish('tool-inspector:updated', snapshot);
                        } catch {
                            // Best-effort debug hook; ignore failures
                        }
                    }

                    return {
                        toolOrigins,
                        toolCounts: counts,
                        panelContext: {
                            ...state.panelContext,
                            toolOrigins,
                        },
                    };
                });
            },

            /**
             * Load available tools from backend
             * Tool counts/origins are derived exclusively via this loader
             * Zustand remains the single source of truth
             */
            loadAvailableTools: async () => {
                try {
                    const toolsResponse = await chatApi.getTools();
                    if (toolsResponse) {
                        const toolOrigins: Record<string, ToolOrigin> = {};
                        for (const tool of toolsResponse.tools ?? []) {
                            const toolName = tool.function?.name;
                            if (toolName && toolsResponse.tool_origins?.[toolName]) {
                                toolOrigins[toolName] = toolsResponse.tool_origins[toolName];
                            }
                        }

                        const toolDefinitions: Record<string, ToolDefinition> = {};
                        for (const tool of toolsResponse.tools ?? []) {
                            const toolName = tool.function?.name;
                            if (toolName) {
                                toolDefinitions[toolName] = {
                                    function: tool.function,
                                };
                            }
                        }

                        set((state) => ({
                            toolOrigins,
                            panelContext: {
                                ...state.panelContext,
                                toolDefinitions,
                                toolOrigins,
                                hasActiveTools: (toolsResponse.tools?.length || 0) > 0,
                            },
                        }));
                    }
                } catch (error) {
                    console.error('[chatStore] Failed to load available tools:', error);
                }
            },

            // ========================================================================
            // ATTACHMENT ACTIONS
            // ========================================================================

            setPendingAttachment: (attachment) => {
                set({ pendingAttachment: attachment });
            },

            uploadAttachment: async (file: File): Promise<Attachment | null> => {
                try {
                    set({ attachmentUploadInProgress: true });
                    const response = await chatApi.uploadAttachment(file);
                    if (response?.file) {
                        const attachment: Attachment = {
                            filename: file.name,
                            mimetype: file.type || 'application/octet-stream',
                            size: file.size,
                            ...response.file,
                        };
                        set({ pendingAttachment: attachment, attachmentUploadInProgress: false });
                        return attachment;
                    }
                    set({ attachmentUploadInProgress: false });
                    return null;
                } catch (error) {
                    set({ attachmentUploadInProgress: false });
                    console.error('[chatStore] Failed to upload attachment:', error);
                    return null;
                }
            },

            // ========================================================================
            // TTS ACTIONS
            // ========================================================================

            setTtsEnabled: (enabled) => {
                set({ ttsEnabled: enabled });
                // Persist to database (non-blocking)
                fetch('/api/config/tts-enabled', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled })
                }).catch(err => console.warn('[chatStore] Failed to persist TTS setting:', err));
            },

            // ========================================================================
            // ERROR HANDLING ACTIONS
            // ========================================================================

            setError: (error) => {
                set({ error });
            },

            setLoading: (loading) => {
                set({ isLoading: loading });
            },
        }),
        { name: 'ChatStore' }
    )
);
