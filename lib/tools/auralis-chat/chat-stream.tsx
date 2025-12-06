/**
 * AI Chat Stream Component
 * 
 * ARCHITECTURE NOTES:
 * - Renders stream events (messages, activities, tool calls, thinking)
 * - Enhanced tool call display with collapsible details
 * - Supports rich formatting (screenshots, thinking, progress)
 * - Matches sophisticated tool visualization from DEV
 * 
 * EVENT TYPES:
 * - MESSAGE: User/assistant chat messages
 * - THOUGHT: Thinking/workflow indicators
 * - ACTIVITY: Tool execution activities
 * - TOOL_CALL: Function call events
 * 
 * VISUAL FEATURES:
 * - Collapsible tool details
 * - Status indicators (running/success/error)
 * - Duration labels
 * - Attachment previews
 * - Streaming indicators
 */

import React from 'react';
import {
    formatTimestamp,
    formatMessage,
    hasMeaningfulContent,
    formatArgsPreview,
    formatDuration,
    formatToolArguments,
    formatToolResult,
    formatAttachmentMeta,
    safeParseJson,
} from './formatting';
import { ASSISTANT_NAME, STREAM_EVENT_TYPES } from './constants';
import type { StreamEvent, ChatMessage, ToolCall, Attachment } from './types';

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

/**
 * Extended tool call type
 * Supports various tool call shapes from different sources
 */
type ToolCallLike = Partial<ToolCall> & {
    name?: string;
    status?: string;
    success?: boolean;
    error?: unknown;
    duration_ms?: number;
    durationMs?: number;
    parsedArguments?: Record<string, unknown>;
    result?: unknown;
    [key: string]: unknown;
};

interface AiChatStreamProps {
    events: StreamEvent[];
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Resolve tool call status
 * Normalizes various status representations
 */
function resolveToolCallStatus(call: ToolCallLike | null | undefined): 'running' | 'success' | 'error' {
    if (!call || typeof call !== 'object') {
        return 'running';
    }

    const explicitStatus = typeof call.status === 'string' ? call.status.toLowerCase() : null;
    if (explicitStatus) {
        if (explicitStatus === 'error' || explicitStatus === 'failed' || explicitStatus === 'failure') {
            return 'error';
        }
        if (explicitStatus === 'success' || explicitStatus === 'completed' || explicitStatus === 'done') {
            return 'success';
        }
        if (explicitStatus === 'running' || explicitStatus === 'pending' || explicitStatus === 'in-progress') {
            return 'running';
        }
    }

    if (call.success === false || call.error) {
        return 'error';
    }
    if (call.success === true || call.result !== undefined) {
        return 'success';
    }

    return 'running';
}

/**
 * Format activity summary
 * Creates readable summary of tool call with args preview
 */
function formatActivitySummary(toolCall: ToolCallLike | null | undefined): string {
    const name = toolCall?.function?.name || 'Tool';
    const args = toolCall?.arguments || toolCall?.parsedArguments || toolCall?.function?.arguments;
    const parsedArgs = typeof args === 'string' ? safeParseJson(args) : args;
    const preview = formatArgsPreview(
        typeof parsedArgs === 'object' && parsedArgs !== null && !Array.isArray(parsedArgs)
            ? (parsedArgs as Record<string, unknown>)
            : null
    );
    if (preview) {
        return `${name}${preview}`;
    }
    return name || '';
}

/**
 * Resolve overall tool activity state
 * Aggregates status across multiple tool calls
 */
function resolveToolActivityState(toolCalls: ToolCallLike[] = []): 'running' | 'error' | 'complete' {
    let hasError = false;
    let hasRunning = false;

    toolCalls.forEach((call) => {
        const status = resolveToolCallStatus(call);
        if (status === 'error') {
            hasError = true;
        } else if (status === 'running') {
            hasRunning = true;
        }
    });

    if (hasError) {
        return 'error';
    }
    if (hasRunning) {
        return 'running';
    }
    return 'complete';
}

// ============================================================================
// RENDER FUNCTIONS
// ============================================================================

/**
 * Render single tool call
 * Collapsible card with input/output/error sections
 */
function renderToolCall(call: ToolCallLike, key: React.Key): React.ReactElement {
    const status = resolveToolCallStatus(call);
    const rawToolName = call?.name ?? call?.function?.name ?? 'tool';
    const toolName = String(rawToolName);
    const duration = call?.duration_ms || call?.durationMs;
    const durationLabel = formatDuration(duration);
    const functionArgs = call?.function?.arguments;
    const parsedFunctionArgs = typeof functionArgs === 'string'
        ? safeParseJson(functionArgs)
        : functionArgs;
    const args =
        call?.arguments ||
        call?.parsedArguments ||
        parsedFunctionArgs;
    const result = call?.result;

    return (
        <details key={key} className="step-card" open={status === 'running' || status === 'error'}>
            <summary className="step-card__summary">
                <div className={`step-card__status step-card__status--${status}`}>
                    {status === 'running' && <span className="step-spinner" />}
                    {status === 'success' && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg>}
                    {status === 'error' && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>}
                </div>
                <div className="step-card__header">
                    <span className="step-card__title">{toolName}</span>
                    <span className="step-card__desc">
                        {status === 'running' ? 'Running...' : status === 'error' ? 'Failed' : 'Completed'}
                    </span>
                </div>
                {durationLabel && <span className="step-card__meta">{durationLabel}</span>}
            </summary>

            <div className="step-card__details">
                {(args ? (
                    <div className="step-card__section">
                        <div className="step-card__section-label">Input</div>
                        <div
                            className="step-card__code"
                            dangerouslySetInnerHTML={{
                                __html: formatToolArguments(args as Record<string, unknown>),
                            }}
                        />
                    </div>
                ) : null) as React.ReactNode}

                {result !== undefined && (
                    <div className="step-card__section">
                        <div className="step-card__section-label">Output</div>
                        <div className="step-card__code" dangerouslySetInnerHTML={{ __html: formatToolResult(result) }} />
                    </div>
                )}

                {typeof call?.error !== 'undefined' && (
                    <div className="step-card__error">
                        <div className="step-card__section-label">Error</div>
                        <div className="step-card__code step-card__code--error">
                            {typeof call.error === 'string' ? call.error : JSON.stringify(call.error)}
                        </div>
                    </div>
                )}
            </div>
        </details>
    );
}

/**
 * Render tool section
 * Collapsible list of tool calls
 */
function renderToolSection(toolCalls: ToolCallLike[] | null | undefined): React.ReactNode {
    if (!Array.isArray(toolCalls) || toolCalls.length === 0) {
        return null;
    }

    return (
        <details className="ai-message__tools">
            <summary className="ai-message__tools-summary">
                <span className="ai-message__tools-icon">&darr;</span>
                Tool Calls ({toolCalls.length})
            </summary>
            <div className="ai-message__tools-list">
                {toolCalls.map((call, idx) => renderToolCall(call, call?.id || idx))}
            </div>
        </details>
    );
}

/**
 * Render tool activity block
 * Compact view of tool execution with expandable details
 */
function renderToolActivityBlock(message: ChatMessage): React.ReactNode {
    const toolCalls: ToolCallLike[] = Array.isArray(message?.toolCalls)
        ? message.toolCalls.map((tc: ToolCall) => ({ ...tc }))
        : [];
    if (toolCalls.length === 0) {
        return null;
    }

    const state = resolveToolActivityState(toolCalls);
    const timestamp = formatTimestamp(message?.timestamp);
    const errorCount = toolCalls.reduce((count, call) => {
        return count + (resolveToolCallStatus(call) === 'error' ? 1 : 0);
    }, 0);
    const totalLabel = `${toolCalls.length} tool call${toolCalls.length === 1 ? '' : 's'}`;

    let summaryText = `Completed ${totalLabel}`;
    if (state === 'running') {
        summaryText = `Running ${totalLabel}`;
    } else if (state === 'error') {
        summaryText = `Completed ${totalLabel} with ${errorCount} issue${errorCount === 1 ? '' : 's'}`;
    }
    const attachmentsSection = renderAttachments(message?.attachments);
    const toolSection = renderToolSection(toolCalls);

    return (
        <div className="ai-exec-block" data-state={state}>
            <div className="ai-exec-block__header">
                <span className="ai-exec-block__label">{ASSISTANT_NAME}</span>
                <span className="ai-exec-block__time">{timestamp}</span>
            </div>
            <div className="ai-exec-block__status">
                {state === 'running' && (
                    <span className="ai-exec-block__indicator" aria-hidden="true">
                        <span className="ai-exec-block__dot" />
                        <span className="ai-exec-block__dot" />
                        <span className="ai-exec-block__dot" />
                    </span>
                )}
                <span className="ai-exec-block__summary">{summaryText}</span>
            </div>
            {attachmentsSection && (
                <div className="ai-exec-block__attachments">{attachmentsSection}</div>
            )}
            {toolSection && (
                <div className="ai-exec-block__log ai-tool-call-log--inline">{toolSection}</div>
            )}
        </div>
    );
}

/**
 * Render attachments list
 * Shows file attachments with metadata
 */
function renderAttachments(attachments: Attachment[] | undefined): React.ReactNode {
    if (!Array.isArray(attachments) || attachments.length === 0) {
        return null;
    }

    return (
        <ul className="ai-attachments">
            {attachments.map((attachment, idx) => {
                const url = attachment?.url;
                const label = attachment?.filename || 'attachment';
                if (!url) {
                    return null;
                }
                return (
                    <li key={idx} className="ai-attachment">
                        <a href={url} target="_blank" rel="noopener">
                            {label}
                        </a>
                        <span className="ai-attachment__meta">
                            {formatAttachmentMeta(attachment)}
                        </span>
                    </li>
                );
            })}
        </ul>
    );
}

/**
 * Render thought item
 * "Thinking..." indicator with collapsible content
 */
function renderThoughtItem(event: StreamEvent): React.ReactNode {
    const status = event.status || (event.streaming ? 'thinking' : 'completed');
    const isCompleted = status === 'completed' || status === 'success';
    const isTyping = event.streaming === true && !isCompleted;
    
    // Handle content from message object or string
    const rawContent = typeof event.message === 'object' && event.message?.content 
        ? event.message.content 
        : (typeof event.message === 'string' ? event.message : '');
    const content = typeof rawContent === 'string' ? rawContent.trim() : '';
    const hasContent = Boolean(content);
    const previewText = hasContent ? `${content.slice(0, 50)}${content.length > 50 ? '...' : ''}` : '';
    const formattedContent = hasContent ? formatMessage(content) : '';

    const duration = (event.completedAt && event.startedAt ? event.completedAt - event.startedAt : null);
    const durationLabel = formatDuration(duration);

    // Determine status class (matches step-card styling)
    const statusClass = isTyping ? 'step-card__status--running' : 
        status === 'completed' || status === 'success' ? 'step-card__status--success' : 'step-card__status';
    const statusLabel = isTyping ? 'Thinking...' : (status === 'error' ? 'Error' : (hasContent ? 'Reasoning complete' : 'Waiting for reasoning'));

    return (
        <div
            className="ai-stream__item ai-stream__item--thought"
            data-event-id={event.id}
            data-status={status}
        >
            {/* Uses step-card styling for consistency with tool calls */}
            <details className="step-card thought-card">
                <summary className="step-card__summary">
                    <div className={`step-card__status ${statusClass}`}>
                        {isTyping ? (
                            <span className="step-spinner" />
                        ) : status === 'completed' ? (
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg>
                        ) : (
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" /></svg>
                        )}
                    </div>
                    <div className="step-card__header">
                        <span className="step-card__title">
                            {isTyping ? (
                                <>
                                    Auralis is thinking
                                    <span className="thinking-dots">
                                        <span className="thinking-dot">.</span>
                                        <span className="thinking-dot">.</span>
                                        <span className="thinking-dot">.</span>
                                    </span>
                                </>
                            ) : (
                                'AI Reasoning'
                            )}
                        </span>
                        {!isTyping && previewText && (
                            <span className="step-card__desc">{previewText}</span>
                        )}
                        {!previewText && !isTyping && (
                            <span className="step-card__desc">{statusLabel}</span>
                        )}
                    </div>
                    {durationLabel && <span className="step-card__meta">{durationLabel}</span>}
                </summary>
                {(hasContent || isTyping) && (
                    <div className="step-card__details">
                        <div className="step-card__section">
                            {hasContent ? (
                                <div
                                    className="step-card__code thought-content"
                                    dangerouslySetInnerHTML={{ __html: formattedContent }}
                                />
                            ) : (
                                <div className="step-card__code thought-content thought-content--empty">
                                    {statusLabel}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </details>
        </div>
    );
}

/**
 * Render activity item
 * Tool execution activity with collapsible details
 */
function renderActivityItem(event: StreamEvent): React.ReactNode {
    const toolCall: ToolCallLike | null = event.toolCall ? (event.toolCall as ToolCallLike) : null;
    const activityStatus = typeof event.activity === 'object'
        ? (event.activity.status || 'running')
        : (event.status || 'running');

    const status = resolveToolCallStatus(toolCall) ||
        (activityStatus === 'error' ? 'error' :
            activityStatus === 'success' || activityStatus === 'completed' ? 'success' : 'running');

    const toolName = toolCall?.function?.name ||
        (typeof event.activity === 'string' ? 'Activity' : event.activity?.tool || 'Activity');

    const summary = toolCall ? formatActivitySummary(toolCall) :
        (typeof event.activity === 'string'
            ? event.activity
            : (typeof event.activity === 'object' ? (event.activity.error || 'Processing...') : 'Processing...'));

    const duration = toolCall?.duration_ms;
    const durationLabel = formatDuration(duration);

    return (
        <div
            className="ai-stream__item ai-stream__item--activity"
            data-event-id={event.id}
            data-status={status}
        >
            <details className="step-card" open={status === 'running' || status === 'error'}>
                <summary className="step-card__summary">
                    <div className={`step-card__status step-card__status--${status}`}>
                        {status === 'running' && <span className="step-spinner" />}
                        {status === 'success' && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12" /></svg>}
                        {status === 'error' && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>}
                    </div>
                    <div className="step-card__header">
                        <span className="step-card__title">{toolName}</span>
                        <span className="step-card__desc">{summary}</span>
                    </div>
                    {durationLabel && <span className="step-card__meta">{durationLabel}</span>}
                </summary>

                <div className="step-card__details">
                    {toolCall && (toolCall.arguments || toolCall.parsedArguments) ? (
                        <div className="step-card__section">
                            <div className="step-card__section-label">Input</div>
                            <div
                                className="step-card__code"
                                dangerouslySetInnerHTML={{
                                    __html: formatToolArguments(toolCall.arguments || toolCall.parsedArguments),
                                }}
                            />
                        </div>
                    ) : null}

                    {toolCall?.result !== undefined ? (
                        <div className="step-card__section">
                            <div className="step-card__section-label">Output</div>
                            <div className="step-card__code" dangerouslySetInnerHTML={{ __html: formatToolResult(toolCall.result) }} />
                        </div>
                    ) : null}

                    {typeof toolCall?.error !== 'undefined' && (
                        <div className="step-card__error">
                            <div className="step-card__section-label">Error</div>
                            <div className="step-card__code step-card__code--error">
                                {typeof toolCall.error === 'string' ? toolCall.error : JSON.stringify(toolCall.error)}
                            </div>
                        </div>
                    )}
                </div>
            </details>
        </div>
    );
}

/**
 * Render chat message
 * User or assistant message with optional tool calls
 */
function renderMessage(msg: ChatMessage, options: { streaming?: boolean } = {}): React.ReactNode {
    const { streaming = false } = options;
    const isUser = msg.role === 'user';
    const timestamp = formatTimestamp(msg.timestamp);
    const formattedContent = formatMessage(msg.content || '');
    const attachmentsSection = renderAttachments(msg.attachments);
    const toolCalls: ToolCallLike[] = Array.isArray(msg.toolCalls)
        ? msg.toolCalls.map((tc: ToolCall) => ({ ...tc }))
        : [];
    const hasContent = hasMeaningfulContent(msg.content);
    const hasTools = toolCalls.length > 0;

    if (isUser) {
        return (
            <div className="ai-message-block ai-message-block--user">
                <div className="ai-message ai-message--user">
                    <div className="ai-message__header">
                        <span className="ai-message__role">You</span>
                        <span className="ai-message__time">{timestamp}</span>
                    </div>
                    <div className="ai-message__content" dangerouslySetInnerHTML={{ __html: formattedContent }} />
                    {attachmentsSection}
                </div>
            </div>
        );
    }

    if (!hasContent && hasTools) {
        return (
            <div className="ai-message-block ai-message-block--assistant">
                {renderToolActivityBlock(msg)}
            </div>
        );
    }

    // Note: Tool calls are rendered separately via ACTIVITY/TOOL_CALL events
    // Don't render them again here to avoid duplicates
    const streamingClass = streaming ? ' ai-message--streaming' : '';

    // Don't show empty bubble while waiting for first token - let thinking indicator handle it
    if (streaming && !hasContent) {
        return null;
    }

    return (
        <div className="ai-message-block ai-message-block--assistant">
            <div className={`ai-message ai-message--assistant${streamingClass}`} data-message-id={`history-${msg.timestamp || ''}`}>
                <div className="ai-message__header">
                    <span className="ai-message__role">{ASSISTANT_NAME}</span>
                    <span className="ai-message__time">{timestamp}</span>
                </div>
                {hasContent && (
                    <div className="ai-message__content" dangerouslySetInnerHTML={{ __html: formattedContent }} />
                )}
                {attachmentsSection}
            </div>
        </div>
    );
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

/**
 * AI Chat Stream Component
 * 
 * BEHAVIOR:
 * - Renders all stream events in order
 * - Supports real-time streaming updates
 * - Shows rich tool execution details
 * - Handles thinking indicators
 * - Preserves scroll position during updates
 */
export function AiChatStream({ events }: AiChatStreamProps) {
    console.log('[AiChatStream] Rendering with', events.length, 'events');

    // Diagnostic logging for debugging
    events.forEach((event, idx) => {
        console.log(`[AiChatStream] Event ${idx}:`, {
            id: event.id,
            type: event.type,
            streaming: event.streaming,
            messageRole: typeof event.message === 'object' ? event.message?.role : 'N/A',
            messageContent: typeof event.message === 'object' ? (event.message?.content?.substring(0, 50) || '<empty>') : 'N/A',
            messageContentLength: typeof event.message === 'object' ? (event.message?.content?.length || 0) : 0,
        });
    });

    return (
        <>
            {events.map((event) => {
                if (event.type === STREAM_EVENT_TYPES.MESSAGE && event.message && typeof event.message !== 'string') {
                    const msg = event.message as ChatMessage;
                    return (
                        <div key={event.id} className={`ai-stream__item ai-stream__item--${msg.role}`}>
                            {renderMessage(msg, { streaming: Boolean(event.streaming) })}
                        </div>
                    );
                }

                if (event.type === STREAM_EVENT_TYPES.THOUGHT) {
                    return <React.Fragment key={event.id}>{renderThoughtItem(event)}</React.Fragment>;
                }

                if (
                    event.type === STREAM_EVENT_TYPES.ACTIVITY ||
                    event.type === STREAM_EVENT_TYPES.TOOL_CALL
                ) {
                    return <React.Fragment key={event.id}>{renderActivityItem(event)}</React.Fragment>;
                }

                return null;
            })}
        </>
    );
}
