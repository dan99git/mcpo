/**
 * AI Chat Panel - Main Component
 * 
 * ARCHITECTURE NOTES:
 * - Main entry point for AI chat interface
 * - Integrates chat store, API services, streaming
 * - Coordinates all hooks and components
 * - Manages panel-specific tool loading
 * 
 * RESPONSIBILITIES:
 * - Compose core UI (stream, input, tool status)
 * - Wire Zustand chatStore state to components
 * - Register message handler for backend communication
 * - React to openPanelId changes to refresh tools
 * - Trigger system prompt builds on panel changes
 * 
 * DEAD CODE REMOVED:
 * - modelConfig state (frontend doesn't manage this)
 * - aiProvider state (backend handles provider selection)
 * - Tool inspector globals (quarantined debugging feature)
 */

import { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useChatStore } from './chat-store';
import { chatApi } from '../../services/api';
import { publish } from '../../utils/eventBus';
import { ASSISTANT_NAME, STREAM_EVENT_TYPES } from './constants';
import { AiChatStream } from './chat-stream';
import { AiChatInput } from './chat-input';
import { ToolStatusBar } from './tool-status';
import ttsPlayer from './tts-service';
import { createMessageHandler } from './message-handler';
import { useChatPanelInitialization } from './use-chat-init';
import { getBaselineToolOrigins } from '../tools/toolRegistry';
import { getDisabledMcpServers, filterMcpTools } from './tool-prefs';
import type { ChatMessage } from './types';
import './styles/dev/ai-chat.css';

// ============================================================================

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export function AiChatPanel() {
    // ========================================================================
    // STORE SUBSCRIPTIONS
    // ========================================================================

    // High-level chat/session slice
    const {
        messages,
        currentSessionId,
        isLoading,
        error,
        addMessage,
        loadMessages,
        setCurrentSessionId,
        createSession,
        setLoading,
        setError,
        clearMessages,
    } = useChatStore((state) => ({
        messages: state.messages,
        currentSessionId: state.currentSessionId,
        isLoading: state.isLoading,
        error: state.error,
        addMessage: state.addMessage,
        loadMessages: state.loadMessages,
        setCurrentSessionId: state.setCurrentSession,
        createSession: state.createSession,
        setLoading: state.setLoading,
        setError: state.setError,
        clearMessages: state.clearMessages,
    }));

    // Streaming/event slice
    const {
        streamEvents,
        isStreaming,
        addStreamEvent,
        updateStreamEvent,
        setActiveStreamingEvent,
        setStreaming,
        setStreamingAbortController,
        cancelStreaming,
    } = useChatStore((state) => ({
        streamEvents: state.streamEvents,
        isStreaming: state.isStreaming,
        addStreamEvent: state.addStreamEvent,
        updateStreamEvent: state.updateStreamEvent,
        setActiveStreamingEvent: state.setActiveStreamingEvent,
        setStreaming: state.setStreaming,
        setStreamingAbortController: state.setStreamingAbortController,
        cancelStreaming: state.cancelStreaming,
    }));

    // Additional store actions
    const setPanelContext = useChatStore((state) => state.setPanelContext);
    const updateToolOrigins = useChatStore((state) => state.updateToolOrigins);
    const registerMessageHandler = useChatStore((state) => state.registerMessageHandler);
    const clearMessageHandler = useChatStore((state) => state.clearMessageHandler);

    // ========================================================================
    // LOCAL STATE
    // ========================================================================

    const streamContainerRef = useRef<HTMLDivElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    // Spotlight effect state for main container
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [opacity, setOpacity] = useState(0);

    // Spotlight effect state for stream container (outer glow)
    const [streamPosition, setStreamPosition] = useState({ x: 0, y: 0 });
    const [streamOpacity, setStreamOpacity] = useState(0);

    // Spotlight effect state for inner border of stream container
    const [streamInnerPosition, setStreamInnerPosition] = useState({ x: 0, y: 0 });
    const [streamInnerOpacity, setStreamInnerOpacity] = useState(0);

    const rafRef = useRef<number | null>(null);
    const streamRafRef = useRef<number | null>(null);
    const streamInnerRafRef = useRef<number | null>(null);

    // ========================================================================
    // EVENT HANDLERS
    // ========================================================================

    const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!containerRef.current) return;
        if (rafRef.current) return;

        const clientX = e.clientX;
        const clientY = e.clientY;
        const div = containerRef.current;

        rafRef.current = requestAnimationFrame(() => {
            const rect = div.getBoundingClientRect();
            setPosition({ x: clientX - rect.left, y: clientY - rect.top });
            rafRef.current = null;
        });
    }, []);

    const handleMouseEnter = () => {
        setOpacity(1);
    };

    const handleMouseLeave = () => {
        setOpacity(0);
    };

    const handleStreamMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
        if (!streamContainerRef.current) return;

        const clientX = e.clientX;
        const clientY = e.clientY;
        const div = streamContainerRef.current;

        // Update outer glow
        if (!streamRafRef.current) {
            streamRafRef.current = requestAnimationFrame(() => {
                const rect = div.getBoundingClientRect();
                setStreamPosition({ x: clientX - rect.left, y: clientY - rect.top });
                streamRafRef.current = null;
            });
        }

        // Update inner glow (sync with outer)
        if (!streamInnerRafRef.current) {
            streamInnerRafRef.current = requestAnimationFrame(() => {
                const rect = div.getBoundingClientRect();
                setStreamInnerPosition({ x: clientX - rect.left, y: clientY - rect.top });
                streamInnerRafRef.current = null;
            });
        }
    }, []);

    const handleStreamMouseEnter = () => {
        setStreamOpacity(1);
        setStreamInnerOpacity(1);
    };

    const handleStreamMouseLeave = () => {
        setStreamOpacity(0);
        setStreamInnerOpacity(0);
    };

    // ========================================================================
    // INITIALIZATION
    // ========================================================================

    // Local adapter to match ChatApiLike interface expected by useChatPanelInitialization
    const chatApiAdapter = useMemo(
        () => ({
            getCurrentSession: () => chatApi.getCurrentSession(),
            getSessions: () => chatApi.getSessions(),
            getSession: async (sessionId: string): Promise<{ messages: ChatMessage[] }> => {
                const result = await chatApi.getSession(sessionId);
                const storedMessages = result?.messages ?? [];

                const messages: ChatMessage[] = storedMessages.map((msg: any) => ({
                    role: msg.role ?? 'user',
                    content: typeof msg.content === 'string' ? msg.content : String(msg.content ?? ''),
                    timestamp: msg.timestamp,
                }));

                return { messages };
            },
            createSession: (title: string) => chatApi.createSession(title),
            getTools: (panelId?: string | null) => chatApi.getTools(panelId),
            toggleMcpServer: (name: string, enabled: boolean) => chatApi.toggleMcpServer(name, enabled),
        }),
        []
    );

    // One-time bootstrap: TTS, session restore, tool seeding
    useChatPanelInitialization({
        chatApi: chatApiAdapter,
        currentSessionId,
        setCurrentSession: setCurrentSessionId,
        createSession,
        loadMessages,
        setError,
        setLoading,
    });

    // ========================================================================
    // TOOL MANAGEMENT
    // ========================================================================

    /**
     * Refresh panel tools
     * Fetches tools for current panel and filters disabled MCP servers
     */
    const refreshPanelTools = useCallback(
        async (panelId?: string | null) => {
            const baselineOrigins = getBaselineToolOrigins();
            const targetPanelId = panelId ?? useChatStore.getState().openPanelId;

            if (!targetPanelId) {
                console.warn('[AiChatPanel] refreshPanelTools skipped: no openPanelId available');
                return;
            }

            // Helper to check if we're still on the same panel
            const isStillActive = () => useChatStore.getState().openPanelId === targetPanelId;

            try {
                const tools = await chatApi.getTools(targetPanelId);

                if (!isStillActive()) return;

                const disabledServers = getDisabledMcpServers();
                const filteredOrigins = tools?.tool_origins
                    ? filterMcpTools(tools.tool_origins, disabledServers)
                    : {};

                updateToolOrigins({ ...baselineOrigins, ...filteredOrigins });
            } catch (error) {
                console.warn('[AiChatPanel] Failed to refresh tools for panel', targetPanelId, error);

                if (!isStillActive()) return;

                updateToolOrigins({ ...baselineOrigins });
            }
        },
        [updateToolOrigins]
    );

    /**
     * Trigger system prompt build
     * Sends panelId to backend to build system prompt
     */
    const ensurePanelPrompt = useCallback(
        async (panelId: string | null | undefined) => {
            if (!panelId) {
                return;
            }

            try {
                const devUserId = import.meta.env.VITE_DEV_AUTH_USER_ID || 'local-dev-user';
                const headers: Record<string, string> = {
                    'Content-Type': 'application/json',
                };
                if (devUserId) {
                    headers['x-user-id'] = devUserId;
                }

                // 1. Build Raw Prompt (Synchronous, fast)
                const buildRes = await fetch('/api/ai/system-prompt/pre-enhanced', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ panelId })
                });

                // 2. Trigger Refinement (Asynchronous, AI-heavy)
                // We trigger this immediately so the agent has a refined prompt ready soon.
                // We don't await it to keep the UI responsive.
                if (buildRes.ok) {
                    fetch('/api/ai/system-prompt/enhanced', {
                        method: 'POST',
                        headers,
                        body: JSON.stringify({ panelId })
                    }).catch(err => console.warn('[AiChatPanel] Refinement trigger failed:', err));
                }
            } catch (err) {
                console.warn('[AiChatPanel] System prompt build failed (non-critical):', err);
            }
        },
        []
    );

    // ========================================================================
    // MESSAGE HANDLER
    // ========================================================================

    const handleSendMessage = useMemo(
        () =>
            createMessageHandler({
                chatApi,
                getStoreState: () => useChatStore.getState(),
                setError,
                createSession,
                addMessage,
                addStreamEvent,
                updateStreamEvent,
                setActiveStreamingEvent,
                setStreaming,
                setStreamingAbortController,
                setPanelContext,
                speak: (text: string) => ttsPlayer.speak(text),
            }),
        [
            setError,
            createSession,
            addMessage,
            addStreamEvent,
            updateStreamEvent,
            setActiveStreamingEvent,
            setStreaming,
            setStreamingAbortController,
            setPanelContext,
        ]
    );

    // Register message handler
    useEffect(() => {
        registerMessageHandler(handleSendMessage);
        return () => {
            clearMessageHandler();
        };
    }, [handleSendMessage, registerMessageHandler, clearMessageHandler]);

    // ========================================================================
    // PANEL CHANGE HANDLING
    // ========================================================================

    /**
     * Sync tools and system prompt when panel changes
     */
    const sync = useCallback(
        async (panelId: string | null | undefined) => {
            if (!panelId) {
                return;
            }

            await refreshPanelTools(panelId);
            await ensurePanelPrompt(panelId);
        },
        [refreshPanelTools, ensurePanelPrompt]
    );

    // Listen for panel changes
    useEffect(() => {
        let lastPanelId = useChatStore.getState().openPanelId;
        if (lastPanelId) {
            sync(lastPanelId);
        }

        const unsubscribe = useChatStore.subscribe((state) => {
            const newPanelId = state.openPanelId;
            if (newPanelId && newPanelId !== lastPanelId) {
                lastPanelId = newPanelId;
                sync(newPanelId);
            }
        });

        return unsubscribe;
    }, [sync]);

    // Listen for MCP config changes
    useEffect(() => {
        const handleMcpConfigChanged = () => {
            const openPanelId = useChatStore.getState().openPanelId;
            if (openPanelId) {
                refreshPanelTools(openPanelId);
            }
        };

        window.addEventListener('mcp-config-changed', handleMcpConfigChanged);
        return () => {
            window.removeEventListener('mcp-config-changed', handleMcpConfigChanged);
        };
    }, [refreshPanelTools]);

    // ========================================================================
    // AUTO-SCROLL
    // ========================================================================

    useEffect(() => {
        if (streamContainerRef.current) {
            streamContainerRef.current.scrollTop = streamContainerRef.current.scrollHeight;
        }
    }, [streamEvents]);

    // ========================================================================
    // RENDER
    // ========================================================================

    return (
        <div
            ref={containerRef}
            className="ai-chat relative"
            onMouseMove={handleMouseMove}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
        >
            {/* Border Glow Effect */}
            <div
                className="pointer-events-none absolute -inset-px z-50 transition duration-300"
                style={{
                    opacity,
                    background: `radial-gradient(300px circle at ${position.x}px ${position.y}px, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 0.5) 20%, transparent 50%)`,
                    maskImage: 'linear-gradient(black, black), linear-gradient(black, black)',
                    maskClip: 'content-box, border-box',
                    padding: '1px',
                    maskComposite: 'exclude',
                    WebkitMaskComposite: 'xor',
                }}
            />

            {/* Messages Stream */}
            <div
                className="ai-stream-shell relative"
                onMouseMove={handleStreamMouseMove}
                onMouseEnter={handleStreamMouseEnter}
                onMouseLeave={handleStreamMouseLeave}
            >
                {/* Border Glow Effect */}
                <div
                    className="pointer-events-none absolute -inset-px z-50 transition duration-300"
                    style={{
                        opacity: streamOpacity,
                        background: `radial-gradient(300px circle at ${streamPosition.x}px ${streamPosition.y}px, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 0.5) 20%, transparent 50%)`,
                        maskImage: 'linear-gradient(black, black), linear-gradient(black, black)',
                        maskClip: 'content-box, border-box',
                        padding: '1px',
                        maskComposite: 'exclude',
                        WebkitMaskComposite: 'xor',
                    }}
                />
                {/* Inner Border Glow Effect (slim, inside the shell border) */}
                <div
                    className="pointer-events-none absolute z-40 transition duration-300"
                    style={{
                        opacity: streamInnerOpacity,
                        background: `radial-gradient(220px circle at ${streamInnerPosition.x}px ${streamInnerPosition.y}px, rgba(255, 255, 255, 0.9) 0%, rgba(255, 255, 255, 0.4) 18%, transparent 48%)`,
                        maskImage: 'linear-gradient(black, black), linear-gradient(black, black)',
                        maskClip: 'padding-box, border-box',
                        padding: '1px',
                        maskComposite: 'exclude',
                        WebkitMaskComposite: 'xor',
                        top: 1,
                        left: 1,
                        right: 1,
                        bottom: 1,
                    }}
                />
                <div
                    ref={streamContainerRef}
                    id="ai-stream"
                    className="ai-stream"
                >
                    {streamEvents.length === 0 && messages.length === 0 ? (
                        <div className="ai-welcome">
                            <p>I'm {ASSISTANT_NAME}, your AI lighting controls partner. Ask me to:</p>
                            <ul>
                                <li>Surface live bus context and explain device behaviour</li>
                                <li>Configure, commission, and fine-tune lighting workflows</li>
                                <li>Monitor telemetry and translate it into actionable insights</li>
                                <li>Control hardware, orchestrate tests, and verify outcomes</li>
                                <li>Troubleshoot issues collaboratively in real time</li>
                            </ul>
                        </div>
                    ) : streamEvents.length > 0 ? (
                        <AiChatStream events={streamEvents} />
                    ) : messages.length > 0 ? (
                        <AiChatStream
                            events={messages.map((msg, idx) => {
                                const parsedTimestamp = msg.timestamp ? new Date(msg.timestamp).getTime() : Number.NaN;
                                const createdAt = Number.isFinite(parsedTimestamp) ? parsedTimestamp : Date.now() + idx;

                                return {
                                    id: `history-${idx}`,
                                    type: STREAM_EVENT_TYPES.MESSAGE,
                                    createdAt,
                                    timestamp: msg.timestamp || new Date().toISOString(),
                                    message: msg,
                                    streaming: false,
                                };
                            })}
                        />
                    ) : null}
                </div>
            </div>

            {/* Error Display */}
            {error && (
                <div className="ai-error">
                    {error}
                </div>
            )}

            {/* Tool Status */}
            <ToolStatusBar />

            <AiChatInput
                onSend={handleSendMessage}
                disabled={isStreaming || isLoading}
                isStreaming={isStreaming}
                onStopStreaming={cancelStreaming}
                onClearHistory={async () => {
                    if (window.confirm('Are you sure you want to clear the chat history?')) {
                        try {
                            // Clear backend session first
                            await chatApi.clearCurrentSession();
                            // Clear local state
                            clearMessages();
                            // Reset current session ID so a new one is created
                            setCurrentSessionId(null);
                            // Notify other components
                            publish('chat-sessions:updated', { reason: 'session-cleared' });
                        } catch (error) {
                            console.error('[AiChatPanel] Failed to clear session:', error);
                            // Still clear local state even if backend fails
                            clearMessages();
                        }
                    }
                }}
                onExportChat={() => {
                    const data = JSON.stringify(messages, null, 2);
                    const blob = new Blob([data], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `chat-export-${new Date().toISOString()}.json`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                }}
            />
        </div>
    );
}
