import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { createServer } from "../dist/server.js";
import { CodeModeService } from "../dist/service.js";

const upstreamTools = [
  {
    name: "kicad_get_version",
    description: "Get KiCad version information.",
    inputSchema: { type: "object", properties: {} },
    annotations: { readOnlyHint: true, idempotentHint: true },
  },
  {
    name: "pcb_add_track",
    description: "Add a copper track to the current PCB.",
    inputSchema: {
      type: "object",
      properties: { net: { type: "string" } },
      required: ["net"],
    },
    annotations: { destructiveHint: true },
  },
];

class FakeUpstream {
  calls = [];
  handler;
  tools = structuredClone(upstreamTools);

  async connect() {
    return structuredClone(this.tools);
  }

  async refreshTools() {
    return structuredClone(this.tools);
  }

  onToolsChanged(handler) {
    this.handler = handler;
  }

  async callTool(name, args) {
    this.calls.push({ name, args: structuredClone(args) });
    return {
      content: [
        {
          type: "text",
          text: "upstream text",
          _meta: { upstreamBlock: true },
        },
        { type: "image", data: "aGVsbG8=", mimeType: "image/png" },
      ],
      structuredContent: { name, args },
      isError: false,
      _meta: { upstreamResult: true },
    };
  }

  async close() {}
}

function textResult(result) {
  const block = result.content.find((item) => item.type === "text");
  assert.ok(block);
  return block.text;
}

test("MCP surface exposes only search, describe, and execute", async () => {
  const upstream = new FakeUpstream();
  const service = new CodeModeService(upstream);
  await service.initialize();
  const server = createServer(service);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();

  await server.connect(serverTransport);
  await client.connect(clientTransport);

  try {
    const listed = await client.listTools();
    assert.deepEqual(
      listed.tools.map((tool) => tool.name).sort(),
      ["describe", "execute", "search"],
    );

    const search = await client.callTool({
      name: "search",
      arguments: { query: "track", limit: 5 },
    });
    const searchPayload = JSON.parse(textResult(search));
    assert.equal(searchPayload.totalAvailable, 2);
    assert.equal(searchPayload.tools[0].name, "pcb_add_track");
    assert.deepEqual(search.structuredContent, searchPayload);

    const describe = await client.callTool({
      name: "describe",
      arguments: { name: "pcb_add_track" },
    });
    const definition = JSON.parse(textResult(describe));
    assert.deepEqual(definition.inputSchema.required, ["net"]);
    assert.deepEqual(describe.structuredContent, { tool: definition });

    const execute = await client.callTool({
      name: "execute",
      arguments: { name: "pcb_add_track", arguments: { net: "GND" } },
    });
    assert.deepEqual(upstream.calls, [
      { name: "pcb_add_track", args: { net: "GND" } },
    ]);
    assert.equal(execute.content[1].type, "image");
    assert.deepEqual(execute.content[0]._meta, { upstreamBlock: true });
    assert.deepEqual(execute.structuredContent, {
      name: "pcb_add_track",
      args: { net: "GND" },
    });
    assert.equal(execute.isError, false);
    assert.deepEqual(execute._meta, { upstreamResult: true });
  } finally {
    await client.close();
    await server.close();
    await service.close();
  }
});

test("search refreshes the live catalog before returning results", async () => {
  const upstream = new FakeUpstream();
  const service = new CodeModeService(upstream);
  await service.initialize();

  upstream.tools.push({
    name: "pcb_run_drc",
    description: "Run design rule checks.",
    inputSchema: { type: "object", properties: {} },
    annotations: { readOnlyHint: true },
  });

  const result = await service.search("drc", 5);
  assert.equal(result.totalAvailable, 3);
  assert.deepEqual(result.tools.map((tool) => tool.name), ["pcb_run_drc"]);
});

test("unknown tools return an MCP tool error without upstream execution", async () => {
  const upstream = new FakeUpstream();
  const service = new CodeModeService(upstream);
  await service.initialize();
  const server = createServer(service);
  const client = new Client({ name: "test-client", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();

  await server.connect(serverTransport);
  await client.connect(clientTransport);

  try {
    const result = await client.callTool({
      name: "execute",
      arguments: { name: "pcb_missing", arguments: {} },
    });
    assert.equal(result.isError, true);
    assert.match(textResult(result), /Unknown KiCad tool/);
    assert.deepEqual(upstream.calls, []);
  } finally {
    await client.close();
    await server.close();
    await service.close();
  }
});
