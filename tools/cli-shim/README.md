# cli-shim

A generic, spec-driven CLI-to-MCP shim. It reads a JSON spec describing a
command-line tool's subcommands and exposes each subcommand as an MCP tool
over stdio. One shim script, many specs — no per-CLI Python code required.

`cli_shim.py` uses the low-level `mcp.server.Server` API and
`mcp.server.stdio.stdio_server` (not FastMCP). Stdlib + the `mcp` package
only.

## Usage

```
python cli_shim.py <path-to-spec.json>
```

On startup the shim loads and validates the spec. Any missing or malformed
key fails loudly to stderr with exit code 1 — there are no silent fallbacks
or inferred defaults beyond the ones documented below.

## Spec format

Top level (all required unless noted):

| key | type | meaning |
|---|---|---|
| `name` | string | MCP server name |
| `description` | string | MCP server instructions |
| `command` | string | absolute path to the CLI executable |
| `default_timeout_sec` | number, optional | fallback timeout in seconds for tools that don't set their own; the shim's own fallback is 120 |
| `workdir` | string, optional | working directory the CLI is launched in |
| `tools` | array | list of tool definitions, see below |

Each tool:

| key | type | meaning |
|---|---|---|
| `name` | string | MCP tool name |
| `description` | string | MCP tool description |
| `args` | array of strings | base argv tokens appended after `command`, in order. A token may contain a `{param_name}` placeholder, which must reference a **required** param on this tool |
| `params` | object, optional | map of param name to param definition, see below |
| `timeout_sec` | number, optional | overrides `default_timeout_sec` for this tool |

Each param:

| key | type | meaning |
|---|---|---|
| `type` | string | one of `string`, `integer`, `number`, `boolean` |
| `description` | string | used as the JSON Schema property description |
| `required` | boolean, optional | default `false` |
| `args` | array of strings | **required if the param is optional.** Token group appended to argv when the param is supplied. May contain `{param_name}` placeholders (only referencing params on the same tool) |

Required params do not carry their own `args` — their `{param}` placeholder
appears directly inside the tool's base `args` list instead.

### How a call is turned into argv

1. Start with `command`.
2. Append the tool's base `args` tokens, in order, substituting any
   `{param}` placeholder with `str(value)` of that required param.
3. For each optional param, in the order it appears in the tool's `params`
   object: if the caller supplied it, append that param's own `args` token
   group (with `{param}` substitution). For a `boolean` param, `true`
   appends the group (typically a single flag token) and `false`/absent
   appends nothing.
4. Run with `subprocess.run(argv, capture_output=True, text=True, timeout=...)`.
   Never `shell=True`.

Unknown placeholders, unknown supplied params, and missing required params
are all rejected as MCP tool errors (`isError: true`), never silently
ignored.

### Result semantics

The tool result is always a single text block: exit code, stdout, and
stderr, clearly labeled. A non-zero exit code is returned as normal
(non-error) content, because for many CLIs a non-zero exit is meaningful
data rather than failure — e.g. `kicad-cli --exit-code-violations` returns
nonzero on found violations, not on error. Only a spawn failure (bad
executable path, etc.) or a timeout is reported as an MCP tool error.

### Minimal example

```json
{
  "name": "example-cli",
  "description": "Shim for example-cli",
  "command": "C:/tools/example-cli.exe",
  "default_timeout_sec": 60,
  "tools": [
    {
      "name": "build",
      "description": "Build a project file.",
      "args": ["build", "{project_file}"],
      "params": {
        "project_file": {
          "type": "string",
          "description": "Path to the project file to build.",
          "required": true
        },
        "verbose": {
          "type": "boolean",
          "description": "Enable verbose output.",
          "required": false,
          "args": ["--verbose"]
        }
      }
    }
  ]
}
```

## kicad-cli spec

`specs/kicad-cli.json` covers KiCad 10.0.4's `kicad-cli.exe`: `version`,
`sch_erc`, `pcb_drc`, and the `sch_export_*` / `pcb_export_*` families
(pdf, svg, netlist, bom, gerbers, drill, step, pos). Flags were taken from
`kicad-cli <sub> <cmd> --help` output for KiCad 10.0.4, not guessed.
`output` is marked required everywhere kicad-cli accepts `-o`/`--output`,
even though kicad-cli itself will silently auto-name the file if omitted —
that auto-naming is not something an MCP caller can predict, so the shim
forces an explicit, discoverable output path instead. `hpgl` plot format is
not included — it was removed in KiCad 10.

## Wiring into mcpo.json

mcpo launches each server as a stdio subprocess. A shim-backed entry points
`command` at a Python interpreter that has the `mcp` package installed
(e.g. this repo's `.venv`), and passes the shim script path followed by the
spec path as `args`:

```json
{
  "kicad-cli": {
    "command": "D:/vibe-coded-projects/mcpo/.venv/Scripts/python.exe",
    "args": [
      "D:/vibe-coded-projects/mcpo/tools/cli-shim/cli_shim.py",
      "D:/vibe-coded-projects/mcpo/tools/cli-shim/specs/kicad-cli.json"
    ]
  }
}
```

## Adding another CLI

1. Run `<cli> <subcommand> --help` for every subcommand you want to expose
   and read the actual output — do not guess flags.
2. Write a new spec file under `specs/`, following the format above.
3. Add a server entry to `mcpo.json` pointing at `cli_shim.py` and the new
   spec path, as shown above.
4. No changes to `cli_shim.py` are needed for a new CLI — it's spec-driven.
