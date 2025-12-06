/**
 * useAuralisPanel Hook - Agent Bridge System
 * 
 * ARCHITECTURE NOTES:
 * - Exposes panel state to agent via window.__AURALIS_VIEW__
 * - Maintains resilient WebSocket connection to /ws/agent
 * - Handles command execution with priority: actionId > DOM selector
 * - Uses panelId (not panelName) for backend alignment
 * 
 * CRITICAL BEHAVIORS:
 * - Connection retries indefinitely with exponential backoff
 * - First connection delayed 1s for gateway readiness
 * - Rate limiting prevents connection spam
 * - Bridge object cleaned up on unmount
 * 
 * WEBSOCKET PROTOCOL:
 * - Sends panelId in messages (backend translates if needed)
 * - Message types: panel_state, command_result, panel_state_update
 * - Receives: command, get_state
 */

import { useEffect, useRef, useCallback } from 'react';
import type { AuralisPanelConfig, Control, AgentCommand, CommandResult, AuralisViewState, AuralisView } from './types';
import { WEBSOCKET_CONFIG } from './constants';

// ============================================================================
// HOOK IMPLEMENTATION
// ============================================================================

export function useAuralisPanel(config: AuralisPanelConfig) {
    const websocketRef = useRef<WebSocket | null>(null);
    const configRef = useRef(config);
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const connectWebSocketRef = useRef<(isFirstAttempt?: boolean) => void>();
    const reconnectAttemptsRef = useRef<number>(0);
    const lastConnectionAttemptRef = useRef<number>(0);

    // Update config ref when config changes
    useEffect(() => {
        configRef.current = config;
    }, [config]);

    // ========================================================================
    // RECONNECTION LOGIC
    // ========================================================================

    /**
     * Calculate reconnect delay with exponential backoff
     * CRITICAL: Never stops retrying - connection is mandatory
     * Pattern: 3s, 6s, 12s, 24s, max 30s
     */
    const getReconnectDelay = useCallback(() => {
        const delay = Math.min(
            WEBSOCKET_CONFIG.BASE_RECONNECT_DELAY * Math.pow(2, Math.min(reconnectAttemptsRef.current, 3)),
            WEBSOCKET_CONFIG.MAX_RECONNECT_DELAY
        );
        return delay;
    }, []);

    // ========================================================================
    // COMMAND EXECUTION
    // ========================================================================

    /**
     * Execute command locally
     * PRIORITY 1: Use panel's onCommand handler if actionId provided (fastest)
     * PRIORITY 2: Fallback to DOM manipulation if selector provided (legacy)
     */
    const executeLocalCommand = useCallback(async (command: AgentCommand): Promise<CommandResult> => {
        const currentConfig = configRef.current;
        console.log('[useAuralisPanel] executeLocalCommand called:', {
            actionId: command.actionId,
            selector: command.selector,
            hasOnCommand: !!currentConfig.onCommand,
            panelId: currentConfig.panelId,
        });
        try {
            // PRIORITY 1: Use panel's invoke() handler if actionId provided
            if (command.actionId && currentConfig.onCommand) {
                console.log('[useAuralisPanel] Delegating to onCommand handler for:', command.actionId);
                const result = await currentConfig.onCommand(command);
                console.log('[useAuralisPanel] onCommand result:', result);
                return result;
            }

            // PRIORITY 2: Fallback to DOM manipulation only if no actionId
            if (command.selector) {
                const element = document.querySelector(command.selector) as HTMLElement;
                if (element) {
                    // Check visibility (with test environment support)
                    const isVisible = element.offsetParent !== null ||
                        (typeof process !== 'undefined' && process.env.NODE_ENV === 'test') ||
                        (typeof import.meta !== 'undefined' && import.meta.env?.MODE === 'test');

                    const isDisabled = 'disabled' in element && (element as HTMLInputElement | HTMLButtonElement).disabled;

                    if (!isDisabled && isVisible) {
                        element.click();
                        return {
                            success: true,
                            data: { action: 'clicked', selector: command.selector },
                            updatedState: currentConfig.getContext()
                        };
                    } else {
                        return {
                            success: false,
                            error: `Element ${command.selector} is disabled or not visible`
                        };
                    }
                } else {
                    return {
                        success: false,
                        error: `Element ${command.selector} not found`
                    };
                }
            }

            // No valid command provided
            return {
                success: false,
                error: 'No actionId or selector provided'
            };
        } catch (error) {
            return {
                success: false,
                error: error instanceof Error ? error.message : 'Command execution failed'
            };
        }
    }, []);

    // ========================================================================
    // WEBSOCKET CONNECTION
    // ========================================================================

    /**
     * Perform WebSocket connection
     * Handles message routing and reconnection on close/error
     */
    const performConnection = useCallback(() => {
        const currentConfig = configRef.current;

        // Clear any existing connection
        if (websocketRef.current) {
            try {
                websocketRef.current.close();
            } catch (e) {
                // Ignore errors when closing
            }
            websocketRef.current = null;
        }

        // Connect to agent WebSocket endpoint
        const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/agent`;
        console.log(`[AuralisPanel] Attempting to connect to: ${wsUrl}`);

        try {
            websocketRef.current = new WebSocket(wsUrl);

            websocketRef.current.onopen = () => {
                console.log(`[AuralisPanel] Connected to agent bridge: ${currentConfig.panelId}`);
                reconnectAttemptsRef.current = 0;

                // Send initial panel state
                if (websocketRef.current) {
                    websocketRef.current.send(JSON.stringify({
                        type: 'panel_state',
                        panelId: currentConfig.panelId, // ← Uses panelId (backend translates)
                        panelVersion: currentConfig.version,
                        surfaceType: currentConfig.surfaceType,
                        context: currentConfig.getContext(),
                        visibleControls: currentConfig.getVisibleControls(),
                        timestamp: new Date().toISOString()
                    }));
                }
            };

            websocketRef.current.onmessage = async (event) => {
                try {
                    const message = JSON.parse(event.data);
                    console.log('[useAuralisPanel] WebSocket message received:', {
                        type: message.type,
                        commandId: message.commandId,
                        command: message.command,
                    });

                    if (message.type === 'command') {
                        // Execute agent command
                        const result = await executeLocalCommand(message.command);

                        // Send result back
                        if (websocketRef.current) {
                            websocketRef.current.send(JSON.stringify({
                                type: 'command_result',
                                panelId: currentConfig.panelId, // ← Uses panelId
                                commandId: message.commandId,
                                result,
                                timestamp: new Date().toISOString()
                            }));
                        }
                    } else if (message.type === 'get_state') {
                        // Send current state
                        if (websocketRef.current) {
                            websocketRef.current.send(JSON.stringify({
                                type: 'panel_state',
                                panelId: currentConfig.panelId, // ← Uses panelId
                                panelVersion: currentConfig.version,
                                surfaceType: currentConfig.surfaceType,
                                context: currentConfig.getContext(),
                                visibleControls: currentConfig.getVisibleControls(),
                                timestamp: new Date().toISOString()
                            }));
                        }
                    }
                } catch (error) {
                    console.error('[AuralisPanel] Error handling WebSocket message:', error);
                }
            };

            websocketRef.current.onclose = () => {
                console.warn(`[AuralisPanel] Disconnected from agent bridge: ${currentConfig.panelId} - RECONNECTING...`);
                reconnectAttemptsRef.current++;

                // CRITICAL: NEVER STOP RETRYING
                const delay = getReconnectDelay();
                reconnectTimeoutRef.current = setTimeout(() => {
                    reconnectTimeoutRef.current = null;
                    connectWebSocketRef.current?.();
                }, delay);
            };

            websocketRef.current.onerror = (error) => {
                console.error(`[AuralisPanel] WebSocket error for ${currentConfig.panelId}:`, error);
                console.error(`[AuralisPanel] Connection failed - will retry (attempt ${reconnectAttemptsRef.current + 1})`);
            };

        } catch (error) {
            reconnectAttemptsRef.current++;
            console.error(`[AuralisPanel] Failed to create WebSocket connection for ${configRef.current.panelId}:`, error);

            // Retry with exponential backoff
            const delay = getReconnectDelay();
            reconnectTimeoutRef.current = setTimeout(() => {
                reconnectTimeoutRef.current = null;
                connectWebSocketRef.current?.();
            }, delay);
        }
    }, [executeLocalCommand, getReconnectDelay]);

    /**
     * Initialize WebSocket connection with rate limiting and startup delay
     */
    const connectWebSocket = useCallback((isFirstAttempt: boolean = false) => {
        const now = Date.now();
        const timeSinceLastAttempt = now - lastConnectionAttemptRef.current;

        // Enforce minimum interval between connection attempts
        if (!isFirstAttempt && timeSinceLastAttempt < WEBSOCKET_CONFIG.MIN_CONNECTION_INTERVAL) {
            const waitTime = WEBSOCKET_CONFIG.MIN_CONNECTION_INTERVAL - timeSinceLastAttempt;
            console.log(`[AuralisPanel] Rate limiting connection attempt - waiting ${waitTime}ms`);
            reconnectTimeoutRef.current = setTimeout(() => {
                reconnectTimeoutRef.current = null;
                connectWebSocket(isFirstAttempt);
            }, waitTime);
            return;
        }

        lastConnectionAttemptRef.current = now;

        // Add delay on first attempt for gateway readiness
        if (isFirstAttempt) {
            console.log(`[AuralisPanel] First connection attempt - delaying ${WEBSOCKET_CONFIG.FIRST_ATTEMPT_DELAY}ms for gateway readiness`);
            reconnectTimeoutRef.current = setTimeout(() => {
                reconnectTimeoutRef.current = null;
                performConnection();
            }, WEBSOCKET_CONFIG.FIRST_ATTEMPT_DELAY);
            return;
        }

        performConnection();
    }, [performConnection]);

    connectWebSocketRef.current = connectWebSocket;

    // ========================================================================
    // STATE UPDATE HELPER
    // ========================================================================

    /**
     * Send state updates when context changes
     * Can be called manually by panels when state changes
     */
    const sendStateUpdate = useCallback(() => {
        const currentConfig = configRef.current;
        if (websocketRef.current && websocketRef.current.readyState === WebSocket.OPEN) {
            websocketRef.current.send(JSON.stringify({
                type: 'panel_state_update',
                panelId: currentConfig.panelId, // ← Uses panelId
                context: currentConfig.getContext(),
                visibleControls: currentConfig.getVisibleControls(),
                timestamp: new Date().toISOString()
            }));
        }
    }, []);

    /**
     * Send an unsolicited tool_result payload (e.g., panel prompt) to the backend.
     */
    const sendToolResult = useCallback((toolCall: unknown) => {
        const currentConfig = configRef.current;
        if (websocketRef.current && websocketRef.current.readyState === WebSocket.OPEN) {
            websocketRef.current.send(JSON.stringify({
                type: 'tool_result',
                panelId: currentConfig.panelId,
                toolCall,
                timestamp: new Date().toISOString()
            }));
        }
    }, []);

    // ========================================================================
    // BRIDGE LIFECYCLE
    // ========================================================================

    /**
     * CONSOLIDATED: Single useEffect for bridge lifecycle management
     * - Exposes window.__AURALIS_VIEW__ with live getters
     * - Starts WebSocket connection
     * - Cleans up on unmount
     */
    useEffect(() => {
        // Expose bridge interface on window with live getters
        window.__AURALIS_VIEW__ = {
            get panelId() {
                return configRef.current.panelId; // ← Uses panelId
            },
            get panelVersion() {
                return configRef.current.version;
            },
            get surfaceType() {
                return configRef.current.surfaceType;
            },
            get context() {
                return configRef.current.getContext();
            },
            get visibleControls(): Control[] {
                return configRef.current.getVisibleControls();
            },
            get viewState(): AuralisViewState | undefined {
                return configRef.current.getViewState ? configRef.current.getViewState() : undefined;
            },
            executeCommand: executeLocalCommand
        } as AuralisView;

        console.log(`[AuralisPanel] Bridge exposed for panel: ${configRef.current.panelId}`);

        // Start connection with first-attempt delay
        connectWebSocket(true);

        // Cleanup on unmount
        return () => {
            delete window.__AURALIS_VIEW__;

            if (websocketRef.current) {
                websocketRef.current.close();
                websocketRef.current = null;
            }

            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current);
                reconnectTimeoutRef.current = null;
            }

            console.log(`[AuralisPanel] Bridge cleaned up for panel: ${configRef.current.panelId}`);
        };
    }, [connectWebSocket, executeLocalCommand]);

    // ========================================================================
    // RETURN API
    // ========================================================================

    return {
        sendStateUpdate,
        executeLocalCommand,
        sendToolResult,
        isConnected: websocketRef.current?.readyState === WebSocket.OPEN
    };
}
