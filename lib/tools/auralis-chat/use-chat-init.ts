/**
 * Chat Panel Initialization Hook
 * 
 * ARCHITECTURE NOTES:
 * - Bootstraps chat panel on mount
 * - Loads TTS preferences from backend
 * - Restores or creates chat session
 * - Seeds tool origins based on active panel
 * 
 * CRITICAL BEHAVIORS:
 * - Uses openPanelId from store as single source of truth
 * - Never guesses or detects active panel
 * - Loads panel-specific tools from backend
 * - Creates local fallback session if backend unavailable
 * 
 * DEAD CODE REMOVED:
 * - Legacy OpenRouter/MiniMax config loading
 * - Frontend provider selection
 * - Panel context fetching (backend owns this now)
 */

import { useEffect } from 'react';
import { useChatStore } from './chat-store';
import type { ChatSession } from './types';
import type { ChatMessage } from './types';
import ttsPlayer from './tts-service';
import { getBaselineToolOrigins } from '../tools/toolRegistry';
import { getDisabledMcpServers, filterMcpTools } from './tool-prefs';
import { publish } from '../../utils/eventBus';
import type { ToolOrigin } from './types';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

/**
 * Tools response from backend
 */
interface ToolsResponse {
    tool_origins?: Record<string, ToolOrigin>;
}

/**
 * Chat API interface
 * Defines backend endpoints used during initialization
 */
interface ChatApiLike {
    getCurrentSession: () => Promise<ChatSession | null>;
    getSessions: () => Promise<ChatSession[]>;
    getSession: (sessionId: string) => Promise<{ messages: ChatMessage[] }>;
    createSession: (title: string) => Promise<ChatSession>;
    getTools: (panelId?: string | null) => Promise<ToolsResponse | null>;
    toggleMcpServer: (name: string, enabled: boolean) => Promise<unknown>;
}

/**
 * Hook dependencies
 * Props passed from AiChatPanel
 */
export interface ChatPanelInitializationDeps {
    chatApi: ChatApiLike;
    currentSessionId: string | null;
    setCurrentSession: (sessionId: string | null) => void;
    createSession: (session: ChatSession) => void;
    loadMessages: (messages: ChatMessage[]) => void;
    setError: (message: string | null) => void;
    setLoading: (loading: boolean) => void;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================



// ============================================================================
// HOOK IMPLEMENTATION
// ============================================================================

/**
 * Initialize chat panel
 * 
 * FLOW:
 * 1. Load TTS preferences from backend
 * 2. Restore or create chat session
 * 3. Load session messages
 * 4. Seed tool origins for active panel
 * 
 * ERROR HANDLING:
 * - TTS loading failure is non-critical (warns only)
 * - Session loading failure creates local fallback
 * - Tool loading failure falls back to baseline tools
 * - Unexpected errors set error state
 */
export function useChatPanelInitialization({
    chatApi,
    currentSessionId,
    setCurrentSession,
    createSession,
    loadMessages,
    setError,
    setLoading,
}: ChatPanelInitializationDeps) {
    useEffect(() => {
        const initialize = async () => {
            try {
                setLoading(true);

                // ====================================================================
                // TTS PREFERENCE BOOTSTRAP
                // ====================================================================
                // Read persisted "tts-enabled" from backend
                // Sync both Zustand state and ttsPlayer instance
                // Non-fatal: failures only log warning
                try {
                    const ttsResponse = await fetch('/api/config/tts-enabled');
                    if (!ttsResponse.ok) {
                        console.warn('[useChatPanelInitialization] TTS endpoint returned:', ttsResponse.status);
                    }
                    const ttsResult = (await ttsResponse.json()) as {
                        success?: boolean;
                        data?: { enabled?: boolean };
                    };
                    if (ttsResult.success && typeof ttsResult.data?.enabled === 'boolean') {
                        console.log('[useChatPanelInitialization] TTS state loaded:', ttsResult.data.enabled);
                        useChatStore.getState().setTtsEnabled(ttsResult.data.enabled);
                        ttsPlayer.setEnabled(ttsResult.data.enabled);
                    } else {
                        console.log('[useChatPanelInitialization] TTS state not found, using default (false)');
                    }
                } catch (ttsErr) {
                    console.warn('[useChatPanelInitialization] TTS setting loading failed (non-critical):', ttsErr);
                }

                // ====================================================================
                // MCP SERVER STATE SYNC
                // ====================================================================
                // Sync frontend localStorage disabled servers with backend
                // This ensures servers disabled in UI stay disabled after page reload
                try {
                    const disabledServers = getDisabledMcpServers();
                    if (disabledServers.length > 0) {
                        console.log('[useChatPanelInitialization] Syncing disabled MCP servers:', disabledServers);
                        // Disable each server that was disabled in localStorage
                        for (const serverName of disabledServers) {
                            try {
                                await chatApi.toggleMcpServer(serverName, false);
                            } catch (toggleErr) {
                                // Server might not exist or already be disabled, that's ok
                                console.warn(`[useChatPanelInitialization] Failed to disable MCP server ${serverName}:`, toggleErr);
                            }
                        }
                    }
                } catch (mcpErr) {
                    console.warn('[useChatPanelInitialization] MCP sync failed (non-critical):', mcpErr);
                }

                // ====================================================================
                // SESSION RESTORE FLOW
                // ====================================================================
                // 1. If currentSessionId exists, hydrate its messages
                // 2. Otherwise, ask backend for current saved session
                // 3. If all fails, create local fallback session
                try {
                    let resolvedSessionId = currentSessionId;

                    if (!resolvedSessionId) {
                        try {
                            const currentSession = await chatApi.getCurrentSession();
                            if (currentSession?.id) {
                                resolvedSessionId = currentSession.id;
                                setCurrentSession(currentSession.id);
                                try {
                                    const sessionData = await chatApi.getSession(currentSession.id);
                                    loadMessages(sessionData.messages || []);
                                } catch (getSessionErr) {
                                    console.warn(
                                        '[useChatPanelInitialization] Failed to load current session data, continuing with empty history:',
                                        getSessionErr
                                    );
                                }
                            }
                        } catch (getCurrentErr) {
                            console.warn('[useChatPanelInitialization] Failed to resolve current session:', getCurrentErr);
                        }
                    } else {
                        try {
                            const sessionData = await chatApi.getSession(resolvedSessionId);
                            loadMessages(sessionData.messages || []);
                        } catch (getSessionErr) {
                            console.warn(
                                '[useChatPanelInitialization] Failed to load session data, continuing with empty history:',
                                getSessionErr
                            );
                        }
                    }
                } catch (sessionErr) {
                    console.warn(
                        '[useChatPanelInitialization] Session loading failed, creating local fallback session:',
                        sessionErr
                    );
                    // Create in-memory "local_*" session when backend unavailable
                    if (!currentSessionId) {
                        const localSessionId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
                        createSession({
                            id: localSessionId,
                            title: 'New conversation',
                            status: 'active',
                        });
                        // Notify saved chats panel
                        publish('chat-sessions:updated', { sessionId: localSessionId, reason: 'session-created' });
                        // New session: open home panel
                        publish('dynamic-panel:open', { panelId: 'home' });
                    }
                }

                // ====================================================================
                // TOOL ORIGINS SEEDING
                // ====================================================================
                // CRITICAL: Panel-driven architecture
                // - Use openPanelId from store as single source of truth
                // - Never guess or detect active panel
                // - Load panel-specific tools from backend
                // - Filter disabled MCP servers
                // 
                // FLOW:
                // 1. Start with baseline tool catalog
                // 2. Fetch panel-specific + MCP tools for openPanelId
                // 3. Filter out disabled MCP servers
                // 4. Merge and update store
                // 
                // REGRESSION PREVENTION:
                // ✓ ALWAYS use chatStore.openPanelId
                // ✓ NEVER hardcode panel ID or use defaults
                // ✓ Reload tools when openPanelId changes
                // ✓ Ensure tool counts match visible panel
                try {
                    const baselineOrigins = getBaselineToolOrigins();
                    let mergedOrigins: Record<string, ToolOrigin> = { ...baselineOrigins };

                    // Read from single source of truth
                    const openPanelId = useChatStore.getState().openPanelId;
                    const tools = await chatApi.getTools(openPanelId);

                    if (tools?.tool_origins) {
                        const disabledServers = getDisabledMcpServers();
                        const filteredOrigins = filterMcpTools(tools.tool_origins, disabledServers);
                        mergedOrigins = { ...baselineOrigins, ...filteredOrigins };
                    }

                    useChatStore.getState().updateToolOrigins(mergedOrigins);
                } catch (toolsErr) {
                    console.warn('[useChatPanelInitialization] Tools loading failed (non-critical):', toolsErr);
                    try {
                        useChatStore.getState().updateToolOrigins(getBaselineToolOrigins());
                    } catch (stateErr) {
                        console.warn('[useChatPanelInitialization] Failed to seed baseline tool catalog:', stateErr);
                    }
                }

                setError(null);
            } catch (err) {
                console.error('[useChatPanelInitialization] Unexpected initialization error:', err);
                const message = err instanceof Error ? err.message : 'Failed to initialize chat';
                setError(message || 'Failed to initialize chat');
            } finally {
                setLoading(false);
            }
        };

        initialize();
    }, [
        chatApi,
        currentSessionId,
        createSession,
        loadMessages,
        setCurrentSession,
        setError,
        setLoading,
    ]);
}
