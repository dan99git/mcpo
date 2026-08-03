# KiCad Pro Code Mode

KiCad Pro Code Mode wraps the existing local `kicad-mcp-pro` server and exposes three MCP tools:

- `search`: search the live upstream tool catalog and return compact matches.
- `describe`: return the complete MCP definition for one upstream tool.
- `execute`: call one upstream tool by name with an arguments object.

The existing KiCad MCP process currently advertises 273 tools in the configured `agent_full` and `experimental` mode. The catalog is read from the real upstream `tools/list` response, so it can change when KiCad IPC becomes available or the upstream package changes.

## Design boundary

This is a local progressive-discovery adaptation of Cloudflare Code Mode. It reduces the model-visible MCP surface to three definitions without evaluating model-written JavaScript. That keeps execution inside the existing MCP argument schemas and avoids placing the local Windows KiCad process inside a JavaScript sandbox.

The wrapper keeps one upstream process alive, refreshes its catalog before searches, follows paginated `tools/list` responses, and listens for tool-list change notifications. Calls are forwarded once. It does not add retries or approval behavior that the upstream server does not provide.

## Build

```powershell
npm install
npm test
```

The compiled server entry point is `dist/index.js`.

## Configuration

The defaults match the existing local KiCad Pro MCP setup. They can be overridden with:

| Variable | Purpose |
| --- | --- |
| `KICAD_PRO_CODE_MODE_UPSTREAM_COMMAND` | Upstream executable. |
| `KICAD_PRO_CODE_MODE_UPSTREAM_ARGS_JSON` | JSON array of upstream arguments. |
| `KICAD_PRO_CODE_MODE_UPSTREAM_CWD` | Upstream working directory. |
| `KICAD_PRO_CODE_MODE_TIMEOUT_MS` | Per-call timeout in milliseconds. |
| `KICAD_MCP_*` | Passed through to the upstream KiCad MCP process. |

The repository `mcpo.json` entry sets the upstream executable plus the current KiCad workspace and CLI paths. The original `kicad` entry remains unchanged.

## Use

Search before loading a large schema:

```json
{"query":"board version", "limit":10}
```

Describe the selected tool:

```json
{"name":"kicad_get_version"}
```

Execute it:

```json
{"name":"kicad_get_version", "arguments":{}}
```

`execute` returns the upstream MCP result unchanged, including content, structured content, metadata, and error state.

## Live smoke test

Build first, set the same `KICAD_MCP_*` values used by the existing server, then run:

```powershell
npm run smoke
```

The smoke test checks that only the three wrapper tools are advertised, searches the live catalog, describes `kicad_get_version`, and executes that read-only tool.
