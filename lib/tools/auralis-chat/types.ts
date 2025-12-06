/**
 * Core Type Definitions for Auralis Chat
 * 
 * ARCHITECTURE NOTES:
 * - All types use `panelId` (not `panelName`) to align with backend
 * - Backend expects: SystemPromptBuilder.ts PanelState interface
 * - WebSocket server translates panelName → panelId internally
 * - Frontend should use panelId consistently for clarity
 */

// ============================================================================
// MESSAGE TYPES
// ============================================================================

/**
 * Chat message structure
 * Represents a single message in the conversation history
 */
export interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
    timestamp?: string;
    attachments?: Attachment[];
    toolCalls?: ToolCall[];
    name?: string;
}

/**
 * File attachment structure
 * Used for images, PDFs, and other file uploads
 */
export interface Attachment {
    filename: string;
    mimetype: string;
    size: number;
    url?: string;
    data?: string;
}

/**
 * Tool call structure
 * Represents a function call made by the AI
 * 
 * ARCHITECTURE NOTE: This is the canonical ToolCall type for auralis-chat feature.
 * The tools/types.ts has its own ToolCall with JsonRecord arguments.
 * Keep these separate as they serve different purposes.
 */
export interface ToolCall {
    id: string;
    type: 'function';
    function: {
        name: string;
        arguments: string;
    };
    /** Parsed arguments object for display/inspection */
    parsedArguments?: Record<string, unknown>;
    /** Tool execution result (set after execution) */
    result?: unknown;
}

// ============================================================================
// STREAM EVENT TYPES
// ============================================================================

/**
 * Stream event structure
 * Represents real-time events during AI response streaming
 * 
 * TYPES:
 * - message: Chat message event
 * - activity: Tool execution activity
 * - tool_call: Tool invocation
 * - thought: AI reasoning/thinking indicator
 * - error: Error event
 * - plan: Multi-step plan
 */
export interface StreamEvent {
    id: string;
    type: 'message' | 'activity' | 'tool_call' | 'thought' | 'error' | 'plan';
    createdAt: number;
    timestamp: string;
    message?: ChatMessage;
    activity?: {
        tool: string;
        status: 'running' | 'success' | 'error';
        result?: unknown;
        error?: string;
    };
    toolCall?: ToolCall;
    streaming?: boolean;
    status?: string;
    kind?: string;
    startedAt?: number;
    completedAt?: number;
}

// ============================================================================
// AGENT BRIDGE TYPES
// ============================================================================

/**
 * Control interface for UI elements
 * Exposed to the agent via window.__AURALIS_VIEW__
 * 
 * BACKEND ALIGNMENT:
 * - Backend SystemPromptBuilder expects `description` property
 * - We include both `label` and `description` for flexibility
 */
export interface Control {
    selector: string;
    label: string;
    description?: string; // Backend uses this for system prompts
    type: 'button' | 'input' | 'toggle' | 'slider' | 'link';
    actionId?: string;
    enabled: boolean;
    value?: unknown;
}

/**
 * Agent command structure
 * Commands sent from backend agent to frontend panel
 */
export interface AgentCommand {
    actionId?: string;
    selector?: string;
    args?: Record<string, unknown>;
}

/**
 * Command execution result
 * Response sent back to agent after command execution
 */
export interface CommandResult {
    success: boolean;
    data?: unknown;
    error?: string;
    updatedState?: AuralisPanelContext;
}

/**
 * Panel context structure
 * Complete state of a panel exposed to the agent
 * 
 * CRITICAL: Uses `panelId` (not `panelName`) to align with backend
 */
export interface AuralisPanelContext {
    panelId: string; // ← Aligned with backend SystemPromptBuilder
    panelVersion: string;
    currentState?: Record<string, unknown>;
    visibleControls?: Control[];
    surroundingText?: string[];
    surfaceType?: 'html' | 'image' | 'pdf';
}

/**
 * View state for surface-specific data
 * Optional additional state beyond the core context
 */
export interface AuralisViewState {
    [key: string]: unknown;
}

/**
 * Complete Auralis View interface
 * This is what gets exposed on window.__AURALIS_VIEW__
 * 
 * CRITICAL: Uses `panelId` consistently throughout
 */
export interface AuralisView {
    panelId: string; // ← Aligned with backend
    panelVersion: string;
    surfaceType: 'html' | 'image' | 'pdf';
    context: AuralisPanelContext;
    visibleControls: Control[];
    viewState?: AuralisViewState;
    executeCommand: (command: AgentCommand) => Promise<CommandResult>;
}

/**
 * Panel configuration for useAuralisPanel hook
 * 
 * CRITICAL: Uses `panelId` (not `name`) to align with backend
 */
export interface AuralisPanelConfig {
    panelId: string; // ← Aligned with backend
    version: string;
    surfaceType: 'html' | 'image' | 'pdf';
    getContext: () => AuralisPanelContext;
    getVisibleControls: () => Control[];
    onCommand: (command: AgentCommand) => Promise<CommandResult>;
    getViewState?: () => AuralisViewState;
}

// ============================================================================
// SESSION TYPES
// ============================================================================

/**
 * Chat session structure
 * Represents a conversation thread
 */
export interface ChatSession {
    id: string;
    title: string;
    status: 'active' | 'resolved' | 'archived';
    archived?: boolean;
    summary?: string;
    messageCount?: number;
    createdAt?: string;
    updatedAt?: string;
}

// ============================================================================
// TOOL TYPES
// ============================================================================

/**
 * Tool origin classification
 * - baseline: Built-in platform tools
 * - panel: Panel-specific tools
 * - mcp: Model Context Protocol tools
 */
export type ToolOrigin = 'baseline' | 'panel' | 'mcp';

/**
 * Tool counts by origin
 * Used for UI display in ToolStatusBar
 */
export interface ToolCounts {
    baseline: number;
    panel: number;
    mcp: number;
    total: number;
}

// ============================================================================
// GLOBAL WINDOW AUGMENTATION
// ============================================================================

/**
 * Extend Window interface to include Auralis View
 * This makes TypeScript aware of window.__AURALIS_VIEW__
 */
declare global {
    interface Window {
        __AURALIS_VIEW__?: AuralisView;
    }
}
