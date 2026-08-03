const DEFAULT_UPSTREAM_ARGS = [
  "--python",
  "3.13",
  "--with",
  "cairosvg",
  "--with",
  "pillow",
  "kicad-mcp-pro==3.25.0",
  "serve",
  "--transport",
  "stdio",
  "--profile",
  "agent_full",
  "--mode",
  "experimental",
  "--no-telemetry",
];

export interface UpstreamConfig {
  command: string;
  args: string[];
  cwd?: string;
  env: Record<string, string>;
  requestTimeoutMs: number;
}

function parseArgs(raw: string | undefined): string[] {
  if (!raw) {
    return [...DEFAULT_UPSTREAM_ARGS];
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON must be a JSON string array: ${message}`,
    );
  }

  if (!Array.isArray(parsed) || parsed.some((value) => typeof value !== "string")) {
    throw new Error(
      "KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON must be a JSON string array",
    );
  }

  return parsed;
}

function parseTimeout(raw: string | undefined): number {
  if (!raw) {
    return 300_000;
  }

  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1_000 || value > 3_600_000) {
    throw new Error(
      "KICAD_PRO_CODE_MODE_TIMEOUT_MS must be an integer from 1000 to 3600000",
    );
  }
  return value;
}

function upstreamEnvironment(source: NodeJS.ProcessEnv): Record<string, string> {
  const forwarded: Record<string, string> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key.startsWith("KICAD_MCP_") && value !== undefined) {
      forwarded[key] = value;
    }
  }
  return forwarded;
}

export function loadConfig(source: NodeJS.ProcessEnv = process.env): UpstreamConfig {
  const command = source.KICAD_PRO_CODE_MODE_UPSTREAM_COMMAND?.trim() || "uvx";
  const cwd = source.KICAD_PRO_CODE_MODE_UPSTREAM_CWD?.trim() || undefined;

  return {
    command,
    args: parseArgs(source.KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON),
    cwd,
    env: upstreamEnvironment(source),
    requestTimeoutMs: parseTimeout(source.KICAD_PRO_CODE_MODE_TIMEOUT_MS),
  };
}

