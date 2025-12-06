/**
 * Tool Policy
 * ============================================================================
 * 
 * Enforces pre-tool acknowledgment, risk assessment, confirmations, and budgets.
 * These settings control "agentic persistence" - how much work the agent does
 * in a single response before stopping.
 * 
 * AGENTIC PERSISTENCE TUNING:
 * - Higher maxToolCallsPerTurn = agent chains more actions together
 * - requirePreToolAck = false allows immediate tool execution
 * - AI_MAX_ITERATIONS controls agent loop cycles (default: 25)
 * 
 * ENVIRONMENT VARIABLES:
 * - AI_MAX_TOOL_CALLS_PER_TURN: Max tools per single LLM response (default: 50)
 * - AI_MAX_ITERATIONS: Max LLM→Tool→LLM cycles (default: 25)
 * 
 * ============================================================================
 */

export interface ToolPolicyConfig {
    requirePreToolAck: boolean;
    defaultTokenBudget: number;
    defaultTimeBudget: number; // milliseconds
    maxToolCallsPerTurn: number;
    confirmDestructiveActions: boolean;
}

// Read from environment for agentic models that need higher limits
const envMaxToolCalls = parseInt(process.env.AI_MAX_TOOL_CALLS_PER_TURN || '50', 10);

const defaultConfig: ToolPolicyConfig = {
    requirePreToolAck: true, // CRITICAL: Always require acknowledgment before tool calls
    defaultTokenBudget: 5000,
    defaultTimeBudget: 30000, // 30 seconds
    maxToolCallsPerTurn: envMaxToolCalls, // Configurable via AI_MAX_TOOL_CALLS_PER_TURN (default: 50)
    confirmDestructiveActions: true,
};

let config = { ...defaultConfig };

/**
 * Set policy configuration
 */
export function setToolPolicyConfig(newConfig: Partial<ToolPolicyConfig>): void {
    config = { ...config, ...newConfig };
}

/**
 * Get policy configuration
 */
export function getToolPolicyConfig(): ToolPolicyConfig {
    return { ...config };
}

/**
 * Check if assistant text exists in current turn
 * Returns true if there is meaningful assistant content before tool calls
 */
export function hasAssistantTextBeforeTools(assistantContent: string): boolean {
    if (!config.requirePreToolAck) {
        return true; // Policy disabled, always allow
    }

    const trimmed = assistantContent.trim();
    return trimmed.length > 0;
}

/**
 * Generate default acknowledgment message
 * Used when orchestrator needs to inject acknowledgment before tool calls
 */
export function generatePreToolAcknowledgment(toolCount: number): string {
    if (toolCount === 1) {
        return "Okay — I'll check that and run the necessary tool.";
    }
    return `Okay — I'll run ${toolCount} tools to handle that.`;
}

/**
 * Check if tool requires confirmation
 * (Simplified local implementation based on naming patterns)
 */
function toolRequiresConfirmation(toolName: string): boolean {
    const highRiskPatterns = ['delete', 'remove', 'clear', 'reset', 'format', 'destroy', 'wipe'];
    const lowerName = toolName.toLowerCase();
    return highRiskPatterns.some((pattern) => lowerName.includes(pattern));
}

/**
 * Check if tool call should be blocked
 */
export function shouldBlockToolCall(
    toolName: string,
    requiresConfirmation?: boolean
): { blocked: boolean; reason?: string } {
    // Check if tool requires confirmation and user hasn't confirmed
    // Note: In backend context, we might not have immediate user confirmation flow.
    // This policy might need to be adapted or just report the block.
    if (config.confirmDestructiveActions && (requiresConfirmation || toolRequiresConfirmation(toolName))) {
        return {
            blocked: true,
            reason: `Tool "${toolName}" requires user confirmation`,
        };
    }

    return { blocked: false };
}

/**
 * Check if tool call count exceeds limit
 */
export function checkToolCallLimit(count: number): { withinLimit: boolean; remaining: number } {
    const limit = config.maxToolCallsPerTurn;
    const remaining = limit - count;
    return {
        withinLimit: remaining >= 0,
        remaining: Math.max(0, remaining),
    };
}
