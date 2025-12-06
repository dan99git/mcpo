import { Router, Request, Response, json } from 'express';
import { ServiceContainerImpl } from '../../core/service-container';
import { TYPES } from '../../core/types';
import { LoggingService } from '../../core/logging';
import { ToolDiscoveryService } from './tool-discovery-service';
import { McpClient } from './mcp/mcp-client';
import { ToolExecutorManager } from './tool-executor-manager';

export function createToolsRoutes(serviceContainer: ServiceContainerImpl): Router {
    const router = Router();
    const logger = serviceContainer.get(TYPES.LoggingService) as LoggingService;

    // GET /api/ai/tools - Discover available tools
    /**
     * CRITICAL: Panel-Specific Tool Discovery
     * ========================================
     * Returns tools available for a specific panel.
     * Frontend MUST send chatStore.openPanelId as query param.
     * 
     * RETURNS:
     * - Baseline tools (always available)
     * - Panel-specific tools (if panelId provided)
     * - MCP tools (from connected servers)
     * 
     * VALIDATION:
     * - panelId is optional but should match chatStore.openPanelId
     * - If panelId missing, only baseline + MCP tools returned
     * 
     * REGRESSION PREVENTION:
     * ✓ Frontend should ALWAYS send openPanelId
     * ✓ Tool counts must match the visible panel
     * ✓ Reload tools when panel changes
     */
    router.get('/tools', async (req: Request, res: Response) => {
        try {
            const toolDiscovery = new ToolDiscoveryService(logger, serviceContainer);

            // Get panel ID from query param - should be chatStore.openPanelId
            const panelId = req.query.panelId as string | undefined;
            
            // DEBUG: Log the panelId we're discovering tools for
            logger.info('[ToolsRoute] Discovering tools for panelId', { panelId: panelId || 'null' });

            const tools = toolDiscovery.discoverTools(panelId || null);

            // Normalize to the frontend-expected shape: { tools: [{ type:'function', function:{...} }], tool_origins }
            const normalizedTools = [
                ...tools.baseline,
                ...tools.panel,
                ...tools.mcp,
            ].map((t) => ({
                type: 'function' as const,
                function: {
                    name: t.name,
                    description: t.description,
                    parameters: t.parameters || { type: 'object', properties: {}, required: [] },
                },
            }));

            // Disabled MCP servers information (optional hint for clients)
            let disabledMcpServers: string[] | undefined;
            try {
                const mcpClient = serviceContainer.get<McpClient>(TYPES.McpClient);
                if (mcpClient && typeof mcpClient.getConfiguredServers === 'function') {
                    const configured = await mcpClient.getConfiguredServers();
                    disabledMcpServers = configured.filter(s => s.persistedDisabled).map(s => s.name);
                } else if (mcpClient && typeof mcpClient.getAvailableServers === 'function') {
                    // Fallback: collect names of servers that are present but have no tools (best effort)
                    const servers = mcpClient.getAvailableServers();
                    disabledMcpServers = servers.map((s: { name: string }) => s.name);
                }
            } catch {
                // ignore - best effort
            }

            res.json({
                success: true,
                data: {
                    tools: normalizedTools,
                    tool_origins: tools.tool_origins,
                    disabled_mcp_servers: disabledMcpServers,
                },
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            logger.error('[Tools] Discovery failed:', error);
            res.status(500).json({
                success: false,
                error: error instanceof Error ? error.message : 'Tool discovery failed',
                timestamp: new Date().toISOString()
            });
        }
    });

    // POST /api/ai/mcp/call - Call an MCP tool directly
    router.post('/mcp/call', json(), async (req: Request, res: Response) => {
        try {
            const body = req.body || {};
            const { toolName, tool, args } = body;
            const resolvedName = typeof (toolName || tool) === 'string' ? (toolName || tool) as string : undefined;

            if (!resolvedName) {
                return res.status(400).json({
                    success: false,
                    error: 'tool_name_required',
                    message: 'Tool name is required'
                });
            }

            type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
            // Ensure args are JSON-serializable to satisfy MCP client expectations
            const safeArgs =
                args && typeof args === 'object'
                    ? (JSON.parse(JSON.stringify(args)) as Record<string, JsonValue>)
                    : ({} as Record<string, JsonValue>);

            const mcpClient = serviceContainer.get<McpClient>(TYPES.McpClient);
            if (!mcpClient) {
                return res.status(503).json({
                    success: false,
                    error: 'mcp_unavailable',
                    message: 'MCP client service is not available'
                });
            }

            const result = await mcpClient.callTool(resolvedName, safeArgs);

            res.json({
                success: true,
                data: result,
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            logger.error('[Tools] MCP call failed:', error as Error);
            res.status(500).json({
                success: false,
                error: error instanceof Error ? error.message : 'MCP tool execution failed',
                timestamp: new Date().toISOString()
            });
        }
    });

    // GET /api/ai/mcp-servers - List MCP servers (connected and available)
    router.get('/mcp-servers', async (_req: Request, res: Response) => {
        try {
            const mcpClient = serviceContainer.get<McpClient>(TYPES.McpClient);
            if (!mcpClient) {
                return res.status(503).json({
                    success: false,
                    error: 'mcp_unavailable',
                    message: 'MCP client service is not available'
                });
            }

            const servers = typeof (mcpClient.getConfiguredServers) === 'function'
                ? await mcpClient.getConfiguredServers()
                : (mcpClient.getAvailableServers?.() || []);
            res.json({
                success: true,
                data: { servers },
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            logger.error('[Tools] MCP servers list failed:', error);
            res.status(500).json({
                success: false,
                error: error instanceof Error ? error.message : 'Failed to list MCP servers',
                timestamp: new Date().toISOString()
            });
        }
    });

    // POST /api/ai/mcp-servers/:name/toggle - Enable/disable a server
    router.post('/mcp-servers/:name/toggle', json(), async (req: Request, res: Response) => {
        const serverName = req.params.name;
        const enabled = Boolean(req.body?.enabled);

        try {
            const mcpClient = serviceContainer.get<McpClient>(TYPES.McpClient);
            if (!mcpClient) {
                return res.status(503).json({
                    success: false,
                    error: 'mcp_unavailable',
                    message: 'MCP client service is not available'
                });
            }

            await mcpClient.toggleServer(serverName, enabled);
            const servers = typeof (mcpClient.getConfiguredServers) === 'function'
                ? await mcpClient.getConfiguredServers()
                : (mcpClient.getAvailableServers?.() || []);

            res.json({
                success: true,
                data: { servers },
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            logger.error('[Tools] MCP toggle failed:', error);
            res.status(500).json({
                success: false,
                error: error instanceof Error ? error.message : 'Failed to toggle MCP server',
                timestamp: new Date().toISOString()
            });
        }
    });

    // POST /api/ai/tools/execute - Unified tool execution (baseline/panel/MCP)
    router.post('/tools/execute', json(), async (req: Request, res: Response) => {
        try {
            const body = req.body || {};
            const toolName = typeof (body.toolName || body.tool) === 'string' ? (body.toolName || body.tool) as string : undefined;
            const originRaw = typeof body.origin === 'string' ? body.origin : 'baseline';
            const origin = originRaw === 'panel' || originRaw === 'mcp' ? originRaw : 'baseline';
            const args = body.args && typeof body.args === 'object' ? (body.args as Record<string, unknown>) : {};
            const panelId = typeof (body.panelId || body.panel_id) === 'string' ? (body.panelId || body.panel_id) as string : null;

            if (!toolName) {
                return res.status(400).json({
                    success: false,
                    error: 'tool_name_required',
                    message: 'Tool name is required'
                });
            }

            const executorManager = new ToolExecutorManager(serviceContainer, logger);
            const result = await executorManager.executeTool(toolName, origin, args, { panelId });

            res.json({
                success: result.success,
                data: result.result,
                error: result.error,
                duration_ms: result.duration_ms,
                timestamp: new Date().toISOString()
            });
        } catch (error) {
            logger.error('[Tools] Execution failed:', error as Error);
            res.status(500).json({
                success: false,
                error: error instanceof Error ? error.message : 'Tool execution failed',
                timestamp: new Date().toISOString()
            });
        }
    });

    return router;
}
