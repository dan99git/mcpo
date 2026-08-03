import assert from "node:assert/strict";
import test from "node:test";
import { collectAllTools } from "../dist/upstream.js";

test("collectAllTools follows pagination", async () => {
  const calls = [];
  const client = {
    async listTools(params) {
      calls.push(params?.cursor ?? null);
      if (!params?.cursor) {
        return {
          tools: [
            {
              name: "first",
              inputSchema: { type: "object", properties: {} },
            },
          ],
          nextCursor: "page-2",
        };
      }
      return {
        tools: [
          {
            name: "second",
            inputSchema: { type: "object", properties: {} },
          },
        ],
      };
    },
  };

  const tools = await collectAllTools(client, 10_000);
  assert.deepEqual(calls, [null, "page-2"]);
  assert.deepEqual(tools.map((tool) => tool.name), ["first", "second"]);
});

test("collectAllTools rejects repeated cursors", async () => {
  const client = {
    async listTools() {
      return { tools: [], nextCursor: "same" };
    },
  };
  await assert.rejects(() => collectAllTools(client, 10_000), /repeated/i);
});
