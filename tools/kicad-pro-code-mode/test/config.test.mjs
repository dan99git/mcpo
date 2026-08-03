import assert from "node:assert/strict";
import test from "node:test";
import { loadConfig } from "../dist/config.js";

test("loadConfig uses the pinned upstream defaults and forwards only KiCad settings", () => {
  const config = loadConfig({
    KICAD_MCP_WORKSPACE_ROOT: "C:/boards",
    KICAD_MCP_KICAD_CLI: "C:/KiCad/kicad-cli.exe",
    UNRELATED_SECRET: "do-not-forward",
  });

  assert.equal(config.command, "uvx");
  assert.ok(config.args.includes("kicad-mcp-pro==3.25.0"));
  assert.deepEqual(config.env, {
    KICAD_MCP_WORKSPACE_ROOT: "C:/boards",
    KICAD_MCP_KICAD_CLI: "C:/KiCad/kicad-cli.exe",
  });
  assert.equal(config.requestTimeoutMs, 300_000);
});

test("loadConfig accepts explicit command, arguments, cwd, and timeout", () => {
  const config = loadConfig({
    KICAD_PRO_CODE_MODE_UPSTREAM_COMMAND: "custom-uvx",
    KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON: '["serve","--transport","stdio"]',
    KICAD_PRO_CODE_MODE_UPSTREAM_CWD: "C:/work",
    KICAD_PRO_CODE_MODE_TIMEOUT_MS: "120000",
  });

  assert.equal(config.command, "custom-uvx");
  assert.deepEqual(config.args, ["serve", "--transport", "stdio"]);
  assert.equal(config.cwd, "C:/work");
  assert.equal(config.requestTimeoutMs, 120_000);
});

test("loadConfig rejects malformed arguments and unsafe timeout values", () => {
  assert.throws(
    () => loadConfig({ KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON: "{}" }),
    /JSON string array/,
  );
  assert.throws(
    () => loadConfig({ KICAD_PRO_CODE_MODE_TIMEOUT_MS: "999" }),
    /integer from 1000/,
  );
});
