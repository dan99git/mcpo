import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import {
  ToolListChangedNotificationSchema,
  type CallToolResult,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import type { UpstreamConfig } from "./config.js";

interface ToolPageClient {
  listTools(
    params?: { cursor?: string },
    options?: { timeout?: number },
  ): Promise<{ tools: Tool[]; nextCursor?: string }>;
}

export async function collectAllTools(
  client: ToolPageClient,
  timeoutMs: number,
): Promise<Tool[]> {
  const tools: Tool[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;

  do {
    const page = await client.listTools(
      cursor ? { cursor } : undefined,
      { timeout: timeoutMs },
    );
    tools.push(...page.tools);
    cursor = page.nextCursor;
    if (cursor) {
      if (seenCursors.has(cursor)) {
        throw new Error(`Upstream repeated tools/list cursor: ${cursor}`);
      }
      seenCursors.add(cursor);
    }
  } while (cursor);

  return tools;
}

export type ToolsChangedHandler = (tools: Tool[]) => void | Promise<void>;

export interface UpstreamBridge {
  connect(): Promise<Tool[]>;
  refreshTools(): Promise<Tool[]>;
  callTool(name: string, args: Record<string, unknown>): Promise<CallToolResult>;
  onToolsChanged(handler: ToolsChangedHandler): void;
  close(): Promise<void>;
}

export class McpUpstreamBridge implements UpstreamBridge {
  private readonly client = new Client({
    name: "kicad-pro-code-mode-upstream-client",
    version: "0.1.0",
  });
  private readonly transport: StdioClientTransport;
  private toolsChangedHandler: ToolsChangedHandler | undefined;
  private refreshPromise: Promise<Tool[]> | undefined;

  constructor(private readonly config: UpstreamConfig) {
    this.transport = new StdioClientTransport({
      command: config.command,
      args: config.args,
      env: config.env,
      ...(config.cwd ? { cwd: config.cwd } : {}),
      stderr: "inherit",
    });

    this.client.setNotificationHandler(
      ToolListChangedNotificationSchema,
      async () => {
        try {
          const tools = await this.refreshTools();
          await this.toolsChangedHandler?.(tools);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          console.error(`[kicad-pro-code-mode] Catalog refresh failed: ${message}`);
        }
      },
    );
  }

  onToolsChanged(handler: ToolsChangedHandler): void {
    this.toolsChangedHandler = handler;
  }

  async connect(): Promise<Tool[]> {
    await this.client.connect(this.transport);
    return this.refreshTools();
  }

  async refreshTools(): Promise<Tool[]> {
    if (!this.refreshPromise) {
      this.refreshPromise = collectAllTools(
        this.client,
        this.config.requestTimeoutMs,
      ).finally(() => {
        this.refreshPromise = undefined;
      });
    }
    return this.refreshPromise;
  }

  async callTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<CallToolResult> {
    const result = await this.client.callTool(
      { name, arguments: args },
      undefined,
      {
        timeout: this.config.requestTimeoutMs,
        resetTimeoutOnProgress: true,
        maxTotalTimeout: this.config.requestTimeoutMs,
      },
    );

    if ("toolResult" in result) {
      throw new Error("Task-based upstream tool results are not supported");
    }
    return result;
  }

  async close(): Promise<void> {
    await this.client.close();
  }
}

