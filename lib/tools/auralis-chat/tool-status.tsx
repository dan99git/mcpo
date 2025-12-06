/**
 * Tool Status Bar Component
 * 
 * ARCHITECTURE NOTES:
 * - Displays live tool counts by origin (baseline/panel/MCP)
 * - Shows active panel ID chip
 * - Provides drill-down popup to view tool lists
 * - Reads from chat store for tool state
 * 
 * BEHAVIOR:
 * - Click total count to see all tools
 * - Click category badges to filter by origin
 * - Popup positioned intelligently (above/below based on space)
 * - ESC or outside click closes popup
 */

import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useChatStore } from './chat-store';

// ============================================================================
// PANEL CHANGE AUTO-TRIGGER CONFIGURATION
// ============================================================================

/**
 * Selector for the "Send Panel Prompt" button in the status bar
 * This button is clicked automatically when the active panel changes
 */
const PANEL_PROMPT_BUTTON_SELECTOR = '[data-status-action="panel-prompt-push"]';

/**
 * Delay (ms) before triggering the panel prompt after panel change
 * Allows DOM to settle and panel to fully initialize
 */
const PANEL_CHANGE_TRIGGER_DELAY_MS = 300;

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

type ToolCategory = 'baseline' | 'panel' | 'mcp' | 'all';

interface CategorisedTool {
    name: string;
    origin: 'baseline' | 'panel' | 'mcp';
}

// ============================================================================
// CONSTANTS
// ============================================================================

const TOOL_POPUP_MAX_WIDTH = 360;
const TOOL_POPUP_MAX_HEIGHT = 360;
const TOOL_POPUP_MARGIN = 12;

/**
 * Non-panel IDs that should be filtered out
 * These are UI chrome elements, not actual panels
 */
const NON_PANEL_IDS = new Set(['activity-bar', 'operations-bar', 'status-bar', 'workspace']);

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Sanitize panel ID
 * Filters out null, empty, and non-panel IDs
 */
const sanitizePanelId = (value?: string | null) => {
    if (!value) {
        return null;
    }
    const trimmed = value.trim();
    if (!trimmed) {
        return null;
    }
    if (NON_PANEL_IDS.has(trimmed.toLowerCase())) {
        return null;
    }
    return trimmed;
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export function ToolStatusBar() {
    const { toolCounts, toolOrigins, openPanelId } = useChatStore();
    const [selectedCategory, setSelectedCategory] = useState<ToolCategory | null>(null);
    const [popupPosition, setPopupPosition] = useState<{ x: number; y: number } | null>(null);

    // Track previous panel ID to detect changes
    const previousPanelIdRef = useRef<string | null>(null);

    /**
     * AUTO-TRIGGER: Click "Send Panel Prompt" button when panel changes
     * 
     * This effect watches openPanelId and triggers the panel prompt button
     * automatically when the active panel changes. This ensures the AI
     * receives the panel context whenever the user navigates to a new panel.
     */
    useEffect(() => {
        const currentPanelId = openPanelId || 'home';
        const previousPanelId = previousPanelIdRef.current;

        // Update ref for next comparison
        previousPanelIdRef.current = currentPanelId;

        // Skip if this is the initial mount (no previous value)
        if (previousPanelId === null) {
            console.log('[ToolStatusBar] Initial panel:', currentPanelId);
            return;
        }

        // Skip if panel hasn't actually changed
        if (currentPanelId === previousPanelId) {
            return;
        }

        console.log('[ToolStatusBar] Panel changed:', previousPanelId, '->', currentPanelId);

        // Delay to allow panel to fully initialize
        const timeoutId = setTimeout(() => {
            const button = document.querySelector(PANEL_PROMPT_BUTTON_SELECTOR) as HTMLButtonElement | null;

            if (button) {
                console.log('[ToolStatusBar] Auto-triggering panel prompt for:', currentPanelId);
                button.click();
            } else {
                console.warn('[ToolStatusBar] Panel prompt button not found:', PANEL_PROMPT_BUTTON_SELECTOR);
            }
        }, PANEL_CHANGE_TRIGGER_DELAY_MS);

        return () => clearTimeout(timeoutId);
    }, [openPanelId]);

    /**
     * Resolve panel label from openPanelId
     * Single source of truth - no dual-source logic
     */
    const resolvedPanelLabel = useMemo(() => {
        return sanitizePanelId(openPanelId) || 'home';
    }, [openPanelId]);

    /**
     * Toggle category popup
     * Calculates intelligent positioning based on available space
     */
    const toggleCategory = (category: ToolCategory, event: React.MouseEvent) => {
        event.stopPropagation();
        const rect = event.currentTarget.getBoundingClientRect();
        const availableWidth = Math.max(TOOL_POPUP_MAX_WIDTH, rect.width);
        const clampedLeft = Math.min(
            Math.max(TOOL_POPUP_MARGIN, rect.left),
            window.innerWidth - availableWidth - TOOL_POPUP_MARGIN
        );

        const popupHeight = Math.min(TOOL_POPUP_MAX_HEIGHT, window.innerHeight - TOOL_POPUP_MARGIN * 2);
        const spaceBelow = window.innerHeight - rect.bottom - TOOL_POPUP_MARGIN;
        const spaceAbove = rect.top - TOOL_POPUP_MARGIN;

        let top: number;
        if (spaceBelow >= popupHeight || spaceBelow >= spaceAbove) {
            top = Math.min(rect.bottom + TOOL_POPUP_MARGIN, window.innerHeight - popupHeight - TOOL_POPUP_MARGIN);
        } else {
            top = Math.max(rect.top - TOOL_POPUP_MARGIN - popupHeight, TOOL_POPUP_MARGIN);
        }

        setSelectedCategory((current) => {
            const nextCategory = current === category ? null : category;
            if (nextCategory) {
                setPopupPosition({ x: clampedLeft, y: top });
            } else {
                setPopupPosition(null);
            }
            return nextCategory;
        });
    };

    /**
     * Close popup on ESC or outside click
     */
    useEffect(() => {
        if (!selectedCategory) {
            return;
        }

        const handleClose = () => {
            setSelectedCategory(null);
            setPopupPosition(null);
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setSelectedCategory(null);
            }
        };

        document.addEventListener('click', handleClose);
        document.addEventListener('keydown', handleKeyDown);

        return () => {
            document.removeEventListener('click', handleClose);
            document.removeEventListener('keydown', handleKeyDown);
        };
    }, [selectedCategory]);

    /**
     * Get tools for selected category
     * Filters by origin or returns all
     */
    const getToolsForCategory = (category: ToolCategory): CategorisedTool[] => {
        const entries = Object.entries(toolOrigins ?? {}) as [string, CategorisedTool['origin']][];

        if (category === 'all') {
            return entries.map(([name, origin]) => ({ name, origin }));
        }

        return entries
            .filter(([, origin]) => origin === category)
            .map(([name, origin]) => ({ name, origin }));
    };

    /**
     * Render category badge
     * Shows count and allows click to filter
     */
    const renderBadge = (
        category: Exclude<ToolCategory, 'all'>,
        count: number,
        label: string
    ) => {
        const badgeClass = `ai-chat__tool-badge ai-chat__tool-badge--${category}`;

        if (count === 0) {
            return (
                <span
                    className={`${badgeClass} ai-chat__tool-badge--disabled`}
                    aria-disabled="true"
                    title={`No ${label} tools available`}
                >
                    <span className="ai-chat__tool-badge-count">0</span>
                    {label}
                </span>
            );
        }

        return (
            <button
                type="button"
                className={`${badgeClass} ai-chat__tool-badge--clickable`}
                onClick={(e) => toggleCategory(category, e)}
                title={`Click to view ${count} ${label} tool${count !== 1 ? 's' : ''}`}
            >
                <span className="ai-chat__tool-badge-count">{count}</span>
                {label}
            </button>
        );
    };

    return (
        <div className="ai-chat__tool-status" style={{ position: 'relative' }}>
            <div className="ai-context-chip ai-context-chip--active" style={{ marginRight: '12px' }}>
                <svg className="ai-context-chip__icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="3" y1="9" x2="21" y2="9"></line>
                </svg>
                {resolvedPanelLabel}
            </div>
            <div className="ai-chat__tool-status-label">Tools:</div>
            <button
                type="button"
                className="ai-chat__tool-count"
                aria-label={`Total tools available: ${toolCounts.total}`}
                disabled={toolCounts.total === 0}
                onClick={(event) => {
                    if (toolCounts.total === 0) {
                        return;
                    }
                    toggleCategory('all', event);
                }}
                title={toolCounts.total === 0 ? 'No tools available' : 'Click to view all tools'}
            >
                {toolCounts.total}
            </button>
            <div className="ai-chat__tool-breakdown">
                {renderBadge('baseline', toolCounts.baseline, 'baseline')}
                {renderBadge('panel', toolCounts.panel, 'panel')}
                {renderBadge('mcp', toolCounts.mcp, 'MCP')}
            </div>

            {selectedCategory && popupPosition && (
                <ToolPopup
                    category={selectedCategory}
                    tools={getToolsForCategory(selectedCategory)}
                    position={popupPosition}
                    onClose={() => setSelectedCategory(null)}
                />
            )}
        </div>
    );
}

// ============================================================================
// TOOL POPUP COMPONENT
// ============================================================================

interface ToolPopupProps {
    category: ToolCategory;
    tools: CategorisedTool[];
    position: { x: number; y: number };
    onClose: () => void;
}

/**
 * Tool list popup
 * Shows filtered tool list with close button
 */
function ToolPopup({ category, tools, position, onClose }: ToolPopupProps) {
    const categoryInfo: Record<ToolCategory, { name: string; class: string }> = {
        baseline: { name: 'Baseline Tools', class: 'baseline' },
        panel: { name: 'Panel Tools', class: 'panel' },
        mcp: { name: 'MCP Tools', class: 'mcp' },
        all: { name: 'All Tools', class: 'all' },
    };

    const info = categoryInfo[category];

    return (
        <div
            className={`ai-chat__tool-popup ai-chat__tool-popup--${info.class}`}
            style={{
                position: 'fixed',
                left: `${position.x}px`,
                top: `${position.y}px`,
                bottom: 'auto',
                right: 'auto',
                zIndex: 40,
            }}
            onClick={(event) => event.stopPropagation()}
        >
            <div className="ai-chat__tool-popup-header">
                <span className="ai-chat__tool-popup-title">{info.name}</span>
                <span className="ai-chat__tool-popup-count">
                    {tools.length} tool{tools.length !== 1 ? 's' : ''}
                </span>
                <button
                    className="ai-chat__tool-popup-close"
                    type="button"
                    aria-label="Close"
                    onClick={onClose}
                >
                    ×
                </button>
            </div>
            <ul className="ai-chat__tool-popup-list">
                {tools.map((tool) => (
                    <li key={`${tool.origin}:${tool.name}`} className="ai-chat__tool-popup-item">
                        <code className="ai-chat__tool-popup-code">{tool.name}</code>
                        {category === 'all' && (
                            <span className="ai-chat__tool-popup-origin">{tool.origin}</span>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}
