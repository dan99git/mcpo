import { Router, Request, Response } from 'express';
import { ServiceContainerImpl } from '../../../core/service-container';
import { TYPES } from '../../../core/types';
import { LoggingService } from '../../../core/logging';
import {
  runAuralisAgentStream,
  type AuralisAgentRunRequest,
  type ExecutedToolCallSummary,
} from './auralis-agent-orchestrator';
import { AIStreamCallbacks } from '../../api-providers/types';
import type { AgentWebSocketServer } from '../../dynamic-panels/agent-websocket';

// Extended Request interface for auth middleware
interface AuthenticatedRequest extends Request {
  userId?: string;
}

function resolveUserId(req: Request): string | null {
  const headerUser = (req.headers['x-user-id'] || req.headers['x-userid']) as string | undefined;
  if (headerUser && headerUser.trim()) {
    return headerUser.trim();
  }

  const requestUserId = (req as AuthenticatedRequest).userId;
  if (requestUserId && requestUserId.trim()) {
    return requestUserId.trim();
  }

  const devUser = process.env.DEV_AUTH_USER_ID;
  if (process.env.NODE_ENV !== 'production' && devUser) {
    return devUser;
  }

  return null;
}

export function createAuralisAgentRoutes(serviceContainer: ServiceContainerImpl): Router {
  const router = Router();
  const logger = serviceContainer.get(TYPES.LoggingService) as LoggingService;

  // NOTE: Streaming SSE endpoint for the backend Auralis agent orchestrator.
  // Mirrors frontend OpenRouter streaming semantics (tokens + tool events).
  // POST /api/ai/auralis/agent/run
  // CORS preflight for SSE POST
  router.options('/auralis/agent/run', (req: Request, res: Response) => {
    res.header('Access-Control-Allow-Origin', req.headers.origin || '*');
    res.header('Access-Control-Allow-Credentials', 'true');

    const defaultHeaders = ['Content-Type', 'Authorization', 'X-User-Id', 'X-No-Compression'];
    const requested = typeof req.headers['access-control-request-headers'] === 'string'
      ? req.headers['access-control-request-headers']
        .split(',')
        .map((h) => h.trim())
        .filter(Boolean)
      : [];
    const allowHeaders = Array.from(new Set([...defaultHeaders, ...requested]));
    res.header('Access-Control-Allow-Headers', allowHeaders.join(', '));

    res.header('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.sendStatus(204);
  });

  router.post('/auralis/agent/run', async (req: Request, res: Response) => {
    try {
      const body = (req.body || {}) as Partial<AuralisAgentRunRequest>;

      const messages = Array.isArray(body.messages) ? body.messages : [];
      if (!messages.length) {
        return res.status(400).json({
          success: false,
          error: 'messages_required',
          message: 'messages array is required and must not be empty',
        });
      }

      const tools = Array.isArray(body.tools) ? body.tools : undefined;
      const toolOrigins = body.toolOrigins && typeof body.toolOrigins === 'object' ? body.toolOrigins : {};
      const maxIterations = typeof body.maxIterations === 'number' ? body.maxIterations : undefined;
      // Use frontend override if provided, otherwise let orchestrator use getActiveProvider()
      const provider = body.provider as 'openrouter' | 'minimax' | undefined;
      const panelId = typeof body.panelId === 'string' ? body.panelId : null;

      logger.info('[AuralisAgentRoutes] Running Auralis backend agent', {
        messageCount: messages.length,
        toolCount: tools?.length || 0,
        maxIterations,
        provider,
        panelId,
      });
      
      // DEBUG: Log toolOrigins to verify frontend is sending them correctly
      const toolOriginEntries = Object.entries(toolOrigins);
      const panelToolCount = toolOriginEntries.filter(([_, origin]) => origin === 'panel').length;
      const baselineToolCount = toolOriginEntries.filter(([_, origin]) => origin === 'baseline').length;
      const mcpToolCount = toolOriginEntries.filter(([_, origin]) => origin === 'mcp').length;
      logger.info('[AuralisAgentRoutes] Tool origins received', {
        totalOrigins: toolOriginEntries.length,
        panelTools: panelToolCount,
        baselineTools: baselineToolCount,
        mcpTools: mcpToolCount,
        samplePanelTools: toolOriginEntries.filter(([_, o]) => o === 'panel').slice(0, 5).map(([name]) => name),
      });
      // Configure SSE response
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('Connection', 'keep-alive');
      res.setHeader('X-Accel-Buffering', 'no'); // Disable Nginx buffering
      
      // CORS headers for SSE (critical for direct gateway connection in dev)
      res.setHeader('Access-Control-Allow-Origin', req.headers.origin || '*');
      res.setHeader('Access-Control-Allow-Credentials', 'true');
      
      // Configure socket for real-time streaming
      if (req.socket) {
        req.socket.setKeepAlive(true);
        req.socket.setNoDelay(true); // Disable Nagle's algorithm to prevent buffering small chunks
      }

      // Flush headers early to avoid proxy buffering and prime the SSE channel
      if (typeof res.flushHeaders === 'function') {
        res.flushHeaders();
      }
      // Send a comment ping so the client knows the stream is live
      res.write(':\n\n');

      // DIAGNOSTIC: Check if flush exists
      const resWithFlush = res as Response & { flush?: () => void };
      logger.info('[AuralisAgentRoutes] Flush method exists?', { exists: typeof resWithFlush.flush === 'function' });

      // Send 2KB of padding to bypass browser/proxy buffering
      // const padding = ': ' + ' '.repeat(2048) + '\n\n';
      // res.write(padding);

      // Emit an initial debug event so clients see something immediately
      const startTs = Date.now();
      res.write(`data: ${JSON.stringify({ type: 'debug', message: 'auralis_stream_start', ts: startTs })}\n\n`);

      if (typeof resWithFlush.flush === 'function') {
        resWithFlush.flush();
      }

      // Helper to send a single SSE event
      const sendEvent = (payload: unknown) => {
        try {
          const success = res.write(`data: ${JSON.stringify(payload)}\n\n`);
          if (!success) {
            logger.warn('[AuralisAgentRoutes] res.write returned false (buffer full)');
            // We should technically wait for 'drain' event here, but for now just logging
          }

          if (typeof resWithFlush.flush === 'function') {
            resWithFlush.flush(); // Ensure immediate flush
          }
        } catch (err) {
          logger.error('[AuralisAgentRoutes] Error writing to stream', err);
        }
      };

      let clientClosed = false;
      req.on('close', () => {
        const stack = new Error().stack;
        logger.warn('[AuralisAgentRoutes] Client connection closed!', {
          timestamp: new Date().toISOString(),
          headersSent: res.headersSent,
          finished: res.finished,
          destroyed: res.destroyed,
          stack
        });
        clientClosed = true;
        clearInterval(keepAlive);
      });

      res.on('error', (err) => {
        logger.error('[AuralisAgentRoutes] Response error!', err);
      });

      res.on('close', () => {
        logger.warn('[AuralisAgentRoutes] Response closed!', {
          timestamp: new Date().toISOString(),
          finished: res.finished
        });
      });

      res.on('finish', () => {
        logger.info('[AuralisAgentRoutes] Response finished!', {
          timestamp: new Date().toISOString()
        });
      });

      // Keep-alive to prevent proxies closing idle streams
      const keepAlive = setInterval(() => {
        if (!clientClosed) {
          res.write(':\n\n');
          if (typeof resWithFlush.flush === 'function') {
            resWithFlush.flush();
          }
        }
      }, 5000); // More frequent keep-alives

      const agentWebSocket = req.app.locals?.agentWebSocket as AgentWebSocketServer | undefined;
      let unsubscribeToolResult: (() => void) | null = null;

      const handlers: AIStreamCallbacks & { onToolExecution?: (summary: ExecutedToolCallSummary) => void } = {
        onToken: (chunk: string) => {
          logger.info('[AuralisAgentRoutes] onToken CALLED', { length: chunk?.length ?? 0, clientClosed });
          if (clientClosed) {
            logger.warn('[AuralisAgentRoutes] onToken blocked - client closed!');
            return;
          }
          logger.info('[AuralisAgentRoutes] onToken sending event', { length: chunk?.length ?? 0 });

          // Write SSE event
          res.write(`data: ${JSON.stringify({ type: 'token', content: chunk })}\n\n`);

          // CRITICAL: Force flush after EVERY token to prevent buffering
          if (typeof resWithFlush.flush === 'function') {
            resWithFlush.flush();
          }
        },
        onReasoning: (chunk: string) => {
          if (clientClosed) return;
          logger.info('[AuralisAgentRoutes] onReasoning', { length: chunk?.length ?? 0 });
          res.write(`data: ${JSON.stringify({ type: 'reasoning', content: chunk })}\n\n`);
          if (typeof resWithFlush.flush === 'function') {
            resWithFlush.flush();
          }
        },
        // CRITICAL: onReasoningDetails for full interleaved thinking support
        // This emits the complete reasoning_details array for rich UI rendering
        onReasoningDetails: (details) => {
          if (clientClosed) return;
          logger.info('[AuralisAgentRoutes] onReasoningDetails', { count: details?.length ?? 0 });
          res.write(`data: ${JSON.stringify({ type: 'reasoning_details', details })}\n\n`);
          if (typeof resWithFlush.flush === 'function') {
            resWithFlush.flush();
          }
        },
        onDebug: (message: string, data?: unknown) => {
          if (clientClosed) return;
          sendEvent({ type: 'debug', message, data });
        },
        onToolCalls: (toolCalls) => {
          if (clientClosed) return;
          logger.info('[AuralisAgentRoutes] onToolCalls', { count: toolCalls?.length ?? 0 });
          sendEvent({ type: 'tool_calls', toolCalls });
        },
        onToolExecution: (summary) => {
          if (clientClosed) return;
          logger.info('[AuralisAgentRoutes] onToolExecution');
          sendEvent({ type: 'tool_result', toolCall: summary });
        },
      };

      if (agentWebSocket && typeof agentWebSocket.onToolResult === 'function') {
        unsubscribeToolResult = agentWebSocket.onToolResult((payload) => {
          if (clientClosed) return;
          sendEvent({
            type: 'tool_result',
            toolCall: payload.toolCall,
            panelId: payload.panelId,
            source: 'panel_bridge',
            timestamp: payload.timestamp ?? new Date().toISOString(),
          });
        });
      }

      const requestUserId =
        (req as AuthenticatedRequest).userId ||
        (process.env.NODE_ENV !== 'production' && process.env.DEV_AUTH_USER_ID
          ? process.env.DEV_AUTH_USER_ID
          : null);

      const userId = resolveUserId(req);

      const result = await runAuralisAgentStream(
        serviceContainer,
        logger,
        {
          messages,
          tools,
          toolOrigins,
          maxIterations,
          provider,
          panelId,
          userId,
        },
        handlers,
      );

      if (!clientClosed) {
        logger.info('[AuralisAgentRoutes] Sending final event', { contentLength: (result.content || '').length, toolCalls: result.toolCalls?.length ?? 0 });
        // Final summary event so client can persist the turn
        sendEvent({
          type: 'final',
          content: result.content,
          toolCalls: result.toolCalls,
          model: result.model,
          timestamp: new Date().toISOString(),
        });
        res.write('data: [DONE]\n\n');
        res.end();
      }
      if (unsubscribeToolResult) {
        unsubscribeToolResult();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      logger.error('[AuralisAgentRoutes] Error running backend agent', error as Error);
      try {
        // If headers were already sent, emit an SSE error; otherwise fall back to JSON.
        if (res.headersSent) {
          res.write(`data: ${JSON.stringify({ type: 'error', error: message })}\n\n`);
          res.write('data: [DONE]\n\n');
          res.end();
        } else {
          res.status(500).json({
            success: false,
            error: message,
            timestamp: new Date().toISOString(),
          });
        }
      } catch {
        // Ignore secondary errors while handling failure
      }
    }
  });

  return router;
}
