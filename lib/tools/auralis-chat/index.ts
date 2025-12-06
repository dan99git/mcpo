/**
 * Auralis Chat - Barrel Exports
 * 
 * ARCHITECTURE NOTES:
 * - Central export point for all chat feature modules
 * - Simplifies imports for consumers
 * - Maintains clean module boundaries
 */

// ============================================================================
// TYPES
// ============================================================================

export type {
    ChatMessage,
    Attachment,
    ToolCall,
    StreamEvent,
    ChatSession,
    Control,
    AgentCommand,
    CommandResult,
    AuralisPanelContext,
    AuralisViewState,
    AuralisView,
    AuralisPanelConfig,
    ToolOrigin,
    ToolCounts,
} from './types';

// ============================================================================
// CONSTANTS
// ============================================================================

export {
    ASSISTANT_NAME,
    STREAM_EVENT_TYPES,
    ALLOWED_UPLOAD_TYPES,
    WEBSOCKET_CONFIG,
} from './constants';

export type { StreamEventType } from './constants';

// ============================================================================
// STATE MANAGEMENT
// ============================================================================

export { useChatStore } from './chat-store';
export type { PanelContext } from './chat-store';

// ============================================================================
// UTILITIES
// ============================================================================

export {
    formatTimestamp,
    formatDuration,
    formatMessage,
    hasMeaningfulContent,
    humanFileSize,
    isAllowedUploadType,
    formatAttachmentMeta,
    formatArgsPreview,
    formatToolArguments,
    formatToolResult,
    safeParseJson,
} from './formatting';

export {
    showWorkflowStatus,
    clearWorkflowStatus,
    delay,
} from './workflow-state';

export {
    getDisabledMcpServers,
    setDisabledMcpServers,
    isMcpServerDisabled,
    toggleMcpServer,
} from './tool-prefs';

// ============================================================================
// HOOKS
// ============================================================================

export { useAuralisPanel } from './use-panel-bridge';

// ============================================================================
// SERVICES
// ============================================================================

export {
    sendAuralisAgentStreamingRequest,
} from './agent-service';

export type {
    StreamingToolCall,
    BackendExecutedToolCallSummary,
    AuralisAgentStreamingResult,
    ToolExecutionResult,
} from './agent-service';

export { transcribeAudio } from './voice-service';
export type { TranscriptionOptions, TranscriptionResponse } from './voice-service';

export { default as ttsPlayer } from './tts-service';
export type { SpeakOptions } from './tts-service';

// ============================================================================
// COMPONENTS
// ============================================================================

export { ToolStatusBar } from './tool-status';

// Note: Main UI components (chat-panel, chat-input, chat-stream)
// and hooks (use-chat-init, message-handler) will be exported
// once they are created
