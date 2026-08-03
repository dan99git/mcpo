import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { CodeModeService } from "./service.js";

function jsonText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function errorResult(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return {
    content: [{ type: "text" as const, text: `Error: ${message}` }],
    isError: true,
  };
}

export function createServer(service: CodeModeService): McpServer {
  const server = new McpServer({
    name: "kicad-pro-code-mode",
    version: "0.1.0",
  });

  server.registerTool(
    "search",
    {
      title: "Search KiCad Pro tools",
      description:
        "Search the live KiCad MCP Pro tool catalog. Returns compact names, descriptions, argument names, required arguments, and safety annotations. Call describe before using an unfamiliar tool.",
      inputSchema: {
        query: z
          .string()
          .optional()
          .default("")
          .describe("Keywords such as pcb track, schematic component, DRC, export, or version. Empty lists tools alphabetically."),
        limit: z
          .number()
          .int()
          .min(1)
          .max(50)
          .optional()
          .default(10)
          .describe("Maximum results, from 1 to 50."),
      },
      annotations: {
        readOnlyHint: true,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ query, limit }) => {
      try {
        const result = await service.search(query, limit);
        return {
          content: [{ type: "text" as const, text: jsonText(result) }],
          structuredContent: { ...result },
        };
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    "describe",
    {
      title: "Describe a KiCad Pro tool",
      description:
        "Return the exact live MCP definition for one KiCad tool, including its complete input schema and safety annotations.",
      inputSchema: {
        name: z.string().min(1).describe("Exact tool name returned by search."),
      },
      annotations: {
        readOnlyHint: true,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ name }) => {
      try {
        const tool = await service.describe(name);
        return {
          content: [{ type: "text" as const, text: jsonText(tool) }],
          structuredContent: { tool },
        };
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    "execute",
    {
      title: "Execute a KiCad Pro tool",
      description:
        "Execute one exact KiCad MCP Pro tool with an arguments object matching its described schema. Results and upstream error status are passed through unchanged. Some tools modify KiCad projects or files.",
      inputSchema: {
        name: z.string().min(1).describe("Exact tool name returned by search."),
        arguments: z
          .record(z.unknown())
          .optional()
          .default({})
          .describe("Arguments matching the tool input schema returned by describe."),
      },
    },
    async ({ name, arguments: toolArguments }) => {
      try {
        return await service.execute(name, toolArguments);
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  return server;
}
