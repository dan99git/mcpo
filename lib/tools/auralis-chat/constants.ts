/**
 * Constants for Auralis Chat
 * 
 * ARCHITECTURE NOTES:
 * - All constants are actively used in the current architecture
 * - Legacy OpenRouter constants have been removed
 * - Stream event types align with backend expectations
 */

// ============================================================================
// DISPLAY CONSTANTS
// ============================================================================

/**
 * Display name for the AI assistant
 * Used in chat UI headers and message attribution
 */
export const ASSISTANT_NAME = 'Auralis';

// ============================================================================
// STREAM EVENT TYPE CONSTANTS
// ============================================================================

/**
 * Canonical stream event type labels
 * Used throughout the UI and message handler for event classification
 * 
 * BACKEND ALIGNMENT:
 * - These types match what the backend sends via SSE
 * - Do not modify without coordinating with backend
 */
export const STREAM_EVENT_TYPES = {
    MESSAGE: 'message',
    ACTIVITY: 'activity',
    TOOL_CALL: 'tool_call',
    THOUGHT: 'thought',
    ERROR: 'error',
    PLAN: 'plan',
} as const;

/**
 * Type-safe stream event type
 * Derived from STREAM_EVENT_TYPES constant
 */
export type StreamEventType = typeof STREAM_EVENT_TYPES[keyof typeof STREAM_EVENT_TYPES];

// ============================================================================
// FILE UPLOAD CONSTANTS
// ============================================================================

/**
 * Allowed MIME types for file attachments
 * Used by attachment picker to validate uploads
 * 
 * SUPPORTED TYPES:
 * - PDFs: application/pdf
 * - Images: png, jpeg, jpg, gif, webp, svg
 */
export const ALLOWED_UPLOAD_TYPES = new Set([
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/gif',
    'image/webp',
    'image/svg+xml',
]);

// ============================================================================
// WEBSOCKET CONSTANTS
// ============================================================================

/**
 * WebSocket reconnection configuration
 * Used by useAuralisPanel hook for resilient connections
 */
export const WEBSOCKET_CONFIG = {
    BASE_RECONNECT_DELAY: 3000, // Start with 3 seconds
    MAX_RECONNECT_DELAY: 30000, // Max 30 seconds between attempts
    MIN_CONNECTION_INTERVAL: 2000, // Minimum 2 seconds between any attempts
    FIRST_ATTEMPT_DELAY: 1000, // 1 second delay on first connection
} as const;
