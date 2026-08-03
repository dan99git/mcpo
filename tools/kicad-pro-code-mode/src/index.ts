#!/usr/bin/env node

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { createServer } from "./server.js";
import { CodeModeService } from "./service.js";
import { McpUpstreamBridge } from "./upstream.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const upstream = new McpUpstreamBridge(config);
  const service = new CodeModeService(upstream);
  const toolCount = await service.initialize();
  const server = createServer(service);
  const transport = new StdioServerTransport();
  let shuttingDown = false;

  const shutdown = async (): Promise<void> => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    await service.close();
    await server.close();
  };

  process.once("SIGINT", () => void shutdown());
  process.once("SIGTERM", () => void shutdown());
  process.stdin.once("end", () => void shutdown());

  await server.connect(transport);
  console.error(
    `[kicad-pro-code-mode] Ready on stdio with ${toolCount} upstream tools and 3 public tools`,
  );
}

main().catch((error) => {
  const message = error instanceof Error ? error.stack ?? error.message : String(error);
  console.error(`[kicad-pro-code-mode] Fatal: ${message}`);
  process.exitCode = 1;
});
