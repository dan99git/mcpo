/**
 * Tool Preferences Utility
 * 
 * ARCHITECTURE NOTES:
 * - Manages MCP (Model Context Protocol) tool visibility settings
 * - Stores disabled MCP servers in localStorage
 * - Provides helpers to check if tools should be hidden
 * - Used by ToolStatusBar to filter displayed tools
 */

// ============================================================================
// CONSTANTS
// ============================================================================

const STORAGE_KEY = 'mcp-disabled-servers';

// ============================================================================
// DISABLED SERVERS MANAGEMENT
// ============================================================================

/**
 * Get list of disabled MCP server names
 * Returns array of server names that should be hidden
 */
export function getDisabledMcpServers(): string[] {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (!stored) {
            return [];
        }
        const parsed = JSON.parse(stored);
        return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
        console.warn('[toolPreferences] Failed to load disabled MCP servers:', err);
        return [];
    }
}

/**
 * Set list of disabled MCP server names
 * Persists to localStorage for cross-session persistence
 * 
 * @param servers - Array of server names to disable
 */
export function setDisabledMcpServers(servers: string[]): void {
    try {
        const normalized = Array.isArray(servers) ? servers : [];
        localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
    } catch (err) {
        console.warn('[toolPreferences] Failed to save disabled MCP servers:', err);
    }
}

/**
 * Check if an MCP server is disabled
 * 
 * @param serverName - Name of the MCP server to check
 * @returns true if server is disabled, false otherwise
 */
export function isMcpServerDisabled(serverName: string): boolean {
    const disabled = getDisabledMcpServers();
    return disabled.includes(serverName);
}

/**
 * Toggle MCP server disabled state
 * Adds to disabled list if currently enabled, removes if currently disabled
 * 
 * @param serverName - Name of the MCP server to toggle
 */
export function toggleMcpServer(serverName: string): void {
    const disabled = getDisabledMcpServers();
    const index = disabled.indexOf(serverName);

    if (index >= 0) {
        // Currently disabled, enable it
        disabled.splice(index, 1);
    } else {
        // Currently enabled, disable it
        disabled.push(serverName);
    }

    setDisabledMcpServers(disabled);
}

// ============================================================================
// TOOL FILTERING
// ============================================================================

import type { ToolOrigin } from './types';

/**
 * Filter MCP tools based on disabled servers
 * Removes tools from disabled MCP servers
 * 
 * @param toolOrigins - Map of tool names to their origins
 * @param disabledServers - List of disabled MCP server names
 * @returns Filtered map of tool origins
 */
export function filterMcpTools(
    toolOrigins: Record<string, ToolOrigin>,
    disabledServers: string[]
): Record<string, ToolOrigin> {
    const filtered: Record<string, ToolOrigin> = {};

    for (const [toolName, origin] of Object.entries(toolOrigins)) {
        // Keep non-MCP tools
        if (origin !== 'mcp') {
            filtered[toolName] = origin;
            continue;
        }

        // For MCP tools, check if server is disabled
        // Tool name format: "server_name__tool_name"
        const serverName = toolName.split('__')[0];
        if (!disabledServers.includes(serverName)) {
            filtered[toolName] = origin;
        }
    }

    return filtered;
}
