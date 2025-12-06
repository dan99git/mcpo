/**
 * Workflow Service
 * 
 * ARCHITECTURE NOTES:
 * - Manages "Thinking..." UI state during AI processing
 * - Creates and updates thought stream events
 * - Provides helpers for workflow status display
 * - Integrates with chat store for state management
 */

import { useChatStore } from './chat-store';
import { STREAM_EVENT_TYPES } from './constants';
import type { StreamEvent } from './types';

// ============================================================================
// WORKFLOW STATUS MANAGEMENT
// ============================================================================

/**
 * Show workflow status (thinking/workflow indicator)
 * 
 * BEHAVIOR:
 * - Updates existing thought event if one is active
 * - Creates new thought event if none exists
 * - Returns the thought event ID for tracking
 * 
 * @param message - Status message to display (e.g., "Analyzing request...")
 * @param type - Status type (default: 'thinking')
 * @returns Thought event ID
 */
export function showWorkflowStatus(
    message: string | null | undefined,
    type: string = 'thinking'
): string {
    const chatStore = useChatStore.getState();
    const now = Date.now();
    const sanitizedMessage = (message || '').trim() || 'Processing...';

    // Check if there's an active thought event
    const activeThoughtId = chatStore.activeThoughtEventId;
    if (activeThoughtId) {
        const existingEvent = chatStore.streamEvents.find((e: StreamEvent) => e.id === activeThoughtId);
        if (existingEvent) {
            // Update existing thought event
            chatStore.updateStreamEvent(activeThoughtId, {
                message: { role: 'assistant', content: sanitizedMessage },
                status: type,
            });
            return activeThoughtId;
        } else {
            // Active thought ID exists but event not found - clear it
            chatStore.setActiveThoughtEvent(null);
        }
    }

    // Create new thought event
    const event: StreamEvent = {
        id: `thought-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`,
        type: STREAM_EVENT_TYPES.THOUGHT,
        createdAt: now,
        timestamp: new Date().toISOString(),
        message: { role: 'assistant', content: sanitizedMessage },
        status: type,
        kind: 'workflow',
        startedAt: now,
    };

    chatStore.addStreamEvent(event);
    chatStore.setActiveThoughtEvent(event.id);
    return event.id;
}

/**
 * Clear workflow status
 * 
 * BEHAVIOR:
 * - Marks current thought event as completed/error
 * - Clears active thought ID
 * - Safe to call even if no active thought exists
 */
export function clearWorkflowStatus(): void {
    const chatStore = useChatStore.getState();
    const activeThoughtId = chatStore.activeThoughtEventId;

    if (!activeThoughtId) {
        return;
    }

    const existingEvent = chatStore.streamEvents.find((event: StreamEvent) => event.id === activeThoughtId);
    if (existingEvent) {
        // Mark as completed
        chatStore.updateStreamEvent(activeThoughtId, {
            status: existingEvent.status === 'error' ? 'error' : 'completed',
            completedAt: Date.now(),
            streaming: false,
        });
    }

    chatStore.setActiveThoughtEvent(null);
}

/**
 * Delay helper for workflow status updates
 * Useful for ensuring UI updates are visible before proceeding
 * 
 * @param ms - Milliseconds to delay
 */
export function delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
}
