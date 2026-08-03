import assert from "node:assert/strict";
import test from "node:test";
import { ToolCatalog } from "../dist/catalog.js";

const tools = [
  {
    name: "pcb_add_track",
    description: "Add a copper track to the current PCB.",
    inputSchema: {
      type: "object",
      properties: { net: { type: "string" }, width: { type: "number" } },
      required: ["net"],
    },
    annotations: { destructiveHint: true },
  },
  {
    name: "sch_add_component",
    description: "Add a component to a schematic.",
    inputSchema: {
      type: "object",
      properties: { symbol: { type: "string" } },
      required: ["symbol"],
    },
  },
  {
    name: "kicad_get_version",
    description: "Get KiCad version information.",
    inputSchema: { type: "object", properties: {} },
    annotations: { readOnlyHint: true, idempotentHint: true },
  },
];

test("search ranks matching names and returns compact argument metadata", () => {
  const catalog = new ToolCatalog();
  catalog.replace(tools);

  const [result] = catalog.search("add track", 10);
  assert.equal(result.name, "pcb_add_track");
  assert.deepEqual(result.argumentNames, ["net", "width"]);
  assert.deepEqual(result.requiredArguments, ["net"]);
  assert.equal("inputSchema" in result, false);
});

test("describe clones the full schema instead of exposing mutable catalog state", () => {
  const catalog = new ToolCatalog();
  catalog.replace(tools);

  const first = catalog.get("pcb_add_track");
  first.inputSchema.required.push("width");
  const second = catalog.get("pcb_add_track");

  assert.deepEqual(second.inputSchema.required, ["net"]);
});

test("duplicate and empty upstream catalogs fail", () => {
  const catalog = new ToolCatalog();
  assert.throws(() => catalog.replace([]), /no tools/i);
  assert.throws(
    () => catalog.replace([tools[0], tools[0]]),
    /duplicate tool name/i,
  );
});

