import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptDir, "..");
const childEnv = Object.fromEntries(
  Object.entries(process.env).filter(
    ([key, value]) =>
      value !== undefined &&
      (key.startsWith("KICAD_MCP_") || key.startsWith("KICAD_PRO_CODE_MODE_")),
  ),
);

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [path.join(projectDir, "dist", "index.js")],
  cwd: projectDir,
  env: childEnv,
  stderr: "inherit",
});
const client = new Client({ name: "kicad-pro-code-mode-smoke", version: "1.0.0" });

try {
  await client.connect(transport);

  const listed = await client.listTools();
  assert.deepEqual(
    listed.tools.map((tool) => tool.name).sort(),
    ["describe", "execute", "search"],
  );

  const searchResult = await client.callTool({
    name: "search",
    arguments: { query: "version", limit: 10 },
  });
  assert.equal(searchResult.isError, undefined);
  const searchPayload = searchResult.structuredContent;
  assert.ok(searchPayload);
  assert.ok(searchPayload.totalAvailable >= 270);
  assert.ok(searchPayload.tools.some((tool) => tool.name === "kicad_get_version"));

  const describeResult = await client.callTool({
    name: "describe",
    arguments: { name: "kicad_get_version" },
  });
  assert.equal(describeResult.isError, undefined);
  assert.equal(describeResult.structuredContent?.tool?.name, "kicad_get_version");

  const executeResult = await client.callTool({
    name: "execute",
    arguments: { name: "kicad_get_version", arguments: {} },
  });
  assert.notEqual(executeResult.isError, true);

  process.stdout.write(
    `${JSON.stringify(
      {
        advertisedTools: listed.tools.map((tool) => tool.name).sort(),
        advertisedDefinitionChars: JSON.stringify(listed.tools).length,
        upstreamCatalogTotal: searchPayload.totalAvailable,
        describedTool: describeResult.structuredContent.tool.name,
        executedTool: "kicad_get_version",
        executeContentTypes: executeResult.content.map((item) => item.type),
      },
      null,
      2,
    )}\n`,
  );
} finally {
  await client.close();
}
