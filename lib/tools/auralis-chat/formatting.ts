/**
 * Formatting Utilities for Auralis Chat
 * 
 * ARCHITECTURE NOTES:
 * - Essential utilities for chat UI rendering
 * - Handles timestamps, file sizes, markdown-like formatting
 * - Tool argument/result display with download links
 * - Browser-Use enhanced result rendering (screenshots, thinking, progress)
 */

import { ALLOWED_UPLOAD_TYPES } from './constants';

// ============================================================================
// TIME FORMATTING
// ============================================================================

/**
 * Format timestamp for display
 * Returns time in HH:MM format
 */
export function formatTimestamp(value: string | Date | null | undefined): string {
    if (!value) {
        return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
        return '';
    }

    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Format duration in milliseconds to human readable string
 * Examples: "150 ms", "2.5 s", "3.2 min", "1.5 h"
 */
export function formatDuration(ms: number | null | undefined): string {
    if (!Number.isFinite(ms as number) || (ms as number) < 0) {
        return '';
    }
    const msValue = ms as number;
    if (msValue < 1000) {
        return `${Math.round(msValue)} ms`;
    }
    const seconds = msValue / 1000;
    if (seconds < 60) {
        return seconds < 10 ? `${seconds.toFixed(1)} s` : `${Math.round(seconds)} s`;
    }
    const minutes = seconds / 60;
    if (minutes < 60) {
        return minutes < 10 ? `${minutes.toFixed(1)} min` : `${Math.round(minutes)} min`;
    }
    const hours = minutes / 60;
    return hours < 10 ? `${hours.toFixed(1)} h` : `${Math.round(hours)} h`;
}

// ============================================================================
// TEXT FORMATTING
// ============================================================================

/**
 * Escape HTML special characters
 * Prevents XSS attacks when rendering user content
 */
function escapeHtml(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Format markdown-like message content
 * Supports: code blocks, inline code, bold, italic
 */
export function formatMessage(content: string | null | undefined): string {
    if (!content) {
        return '';
    }

    // Escape HTML first
    const escaped = escapeHtml(content);

    // Apply markdown-like formatting
    return escaped
        .replace(/```([\s\S]*?)```/g, '<pre class="code-block">$1</pre>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>')
        .replace(/\n/g, '<br>');
}

/**
 * Check if text content has meaningful content (letters or numbers)
 * Used to filter out whitespace-only or symbol-only strings
 */
export function hasMeaningfulContent(text: string | null | undefined): boolean {
    if (!text || typeof text !== 'string') {
        return false;
    }
    const trimmed = text.trim();
    if (!trimmed) {
        return false;
    }
    return /[\p{L}\p{N}]/u.test(trimmed);
}

// ============================================================================
// FILE FORMATTING
// ============================================================================

/**
 * Convert bytes to human readable size
 * Examples: "1.5 KB", "2.3 MB", "1 GB"
 */
export function humanFileSize(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) {
        return '0 B';
    }
    const units = ['B', 'KB', 'MB', 'GB'];
    const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, exponent);
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[exponent]}`;
}

/**
 * Check if MIME type is allowed for upload
 * Uses ALLOWED_UPLOAD_TYPES constant
 */
export function isAllowedUploadType(mime: string | null | undefined): boolean {
    if (!mime) {
        return false;
    }
    const normalized = mime.split(';')[0].trim().toLowerCase();
    return ALLOWED_UPLOAD_TYPES.has(normalized);
}

/**
 * Format attachment metadata for display
 * Returns: "image/png · 1.5 MB"
 */
export function formatAttachmentMeta(attachment: { mimetype?: string; size?: number } | null | undefined): string {
    const mime = attachment?.mimetype || '';
    const size = attachment?.size;
    const parts: string[] = [];
    if (mime) {
        parts.push(mime);
    }
    if (typeof size === 'number' && Number.isFinite(size)) {
        parts.push(humanFileSize(size));
    }
    return parts.join(' · ');
}

// ============================================================================
// TOOL FORMATTING
// ============================================================================

/**
 * Format tool arguments preview for display
 * Shows first 3 arguments inline: "(arg1=value1, arg2=value2, …)"
 */
export function formatArgsPreview(args: Record<string, unknown> | null | undefined): string {
    if (!args || typeof args !== 'object') {
        return '';
    }
    const keys = Object.keys(args);
    if (keys.length === 0) {
        return '';
    }
    const preview = keys.slice(0, 3).map(key => `${key}=${JSON.stringify(args[key])}`).join(', ');
    const suffix = keys.length > 3 ? ', …' : '';
    return ` (${preview}${suffix})`;
}

/**
 * Format tool arguments for display with download link
 * Returns HTML with collapsible code block and download button
 */
export function formatToolArguments(args: unknown): string {
    try {
        if (args === null || args === undefined) {
            return '';
        }
        const payload = typeof args === 'string' ? args : JSON.stringify(args, null, 2);
        const downloadHref = `data:application/json;charset=utf-8,${encodeURIComponent(payload)}`;
        return `
      <div class="step-card__section">
        <div class="step-card__section-header">
          <span>Arguments</span>
          <a class="step-card__download" href="${downloadHref}" download="tool-arguments.json">Download</a>
        </div>
        <code class="step-card__code">${escapeHtml(payload)}</code>
      </div>
    `;
    } catch (err) {
        return '';
    }
}

/**
 * Format tool result for display with download link
 * 
 * ENHANCED RESULT HANDLING:
 * - Screenshots: Renders inline images from data URLs
 * - Thinking: Shows agent reasoning in special section
 * - Progress: Displays step counters (e.g., "Step 2 of 5")
 * - Standard: JSON/text with download link
 */
export function formatToolResult(result: unknown): string {
    if (result === undefined) {
        return '';
    }

    // Handle Browser-Use enhanced result structure
    if (typeof result === 'object' && result !== null && !Array.isArray(result)) {
        const enhanced = result as Record<string, unknown>;

        // Check for screenshot data in common property names: screenshot, url, image, dataUrl
        const screenshotKey = ['screenshot', 'url', 'image', 'dataUrl'].find(
            key => typeof enhanced[key] === 'string' && (enhanced[key] as string).startsWith('data:image')
        );

        // If result contains screenshot data, render it as an image
        if (screenshotKey) {
            const screenshotData = enhanced[screenshotKey] as string;
            const screenshotHtml = `
        <div class="step-card__section">
          <div class="step-card__section-header">
            <span>Screenshot</span>
          </div>
          <img src="${screenshotData}" alt="Screenshot" class="step-card__screenshot" style="max-width: 100%; border-radius: 4px; cursor: pointer;" onclick="window.open(this.src, '_blank')" title="Click to open full size" />
        </div>
      `;

            // Build result HTML with screenshot + other data (excluding the screenshot itself)
            const otherData: Record<string, unknown> = {};
            Object.keys(enhanced).forEach(key => {
                if (key !== screenshotKey) {
                    otherData[key] = enhanced[key];
                }
            });

            const hasOtherData = Object.keys(otherData).length > 0;
            const otherDataHtml = hasOtherData ? formatToolResult(otherData) : '';

            return screenshotHtml + otherDataHtml;
        }

        // If result contains thinking/reasoning, render it
        if (typeof enhanced.thinking === 'string' && enhanced.thinking.trim()) {
            const thinkingHtml = `
        <div class="step-card__section">
          <div class="step-card__section-header">
            <span>Agent Thinking</span>
          </div>
          <div class="step-card__thinking">${escapeHtml(enhanced.thinking)}</div>
        </div>
      `;

            const otherData: Record<string, unknown> = {};
            Object.keys(enhanced).forEach(key => {
                if (key !== 'thinking') {
                    otherData[key] = enhanced[key];
                }
            });

            const hasOtherData = Object.keys(otherData).length > 0;
            const otherDataHtml = hasOtherData ? formatToolResult(otherData) : '';

            return thinkingHtml + otherDataHtml;
        }

        // If result contains step_number, show progress
        if (typeof enhanced.step_number === 'number') {
            const steps = typeof enhanced.steps === 'number' ? enhanced.steps : 0;
            const progressHtml = `
        <div class="step-card__section">
          <div class="step-card__section-header">
            <span>Progress</span>
          </div>
          <div class="step-card__progress">
            Step ${enhanced.step_number}${steps > 0 ? ` of ${steps}` : ''}
          </div>
        </div>
      `;

            const otherData: Record<string, unknown> = {};
            Object.keys(enhanced).forEach(key => {
                if (key !== 'step_number' && key !== 'steps') {
                    otherData[key] = enhanced[key];
                }
            });

            const hasOtherData = Object.keys(otherData).length > 0;
            const otherDataHtml = hasOtherData ? formatToolResult(otherData) : '';

            return progressHtml + otherDataHtml;
        }
    }

    // Handle string results
    if (typeof result === 'string') {
        const downloadHref = `data:text/plain;charset=utf-8,${encodeURIComponent(result)}`;
        return `
      <div class="step-card__section">
        <div class="step-card__section-header">
          <span>Result</span>
          <a class="step-card__download" href="${downloadHref}" download="tool-result.txt">Download</a>
        </div>
        <code class="step-card__code">${escapeHtml(result)}</code>
      </div>
    `;
    }

    // Handle object results
    try {
        const normalized = typeof result === 'object' ? result : { value: result };
        const payload = JSON.stringify(normalized, null, 2);
        const downloadHref = `data:application/json;charset=utf-8,${encodeURIComponent(payload)}`;
        return `
      <div class="step-card__section">
        <div class="step-card__section-header">
          <span>Result</span>
          <a class="step-card__download" href="${downloadHref}" download="tool-result.json">Download</a>
        </div>
        <code class="step-card__code">${escapeHtml(payload)}</code>
      </div>
    `;
    } catch (err) {
        return '';
    }
}

// ============================================================================
// JSON UTILITIES
// ============================================================================

/**
 * Safely parse JSON string
 * Returns fallback value if parsing fails
 */
export function safeParseJson<T = unknown>(input: string | null | undefined, fallback: T = null as T): T {
    if (typeof input !== 'string' || !input.trim()) {
        return fallback;
    }
    try {
        return JSON.parse(input);
    } catch (err) {
        return fallback;
    }
}
