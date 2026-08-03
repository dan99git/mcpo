# Model Capability Forge

## Current boundary

MCPO now has the safe foundation for model-created capabilities:

- canonical `SKILL.md` discovery for bundled, local, and installed packages;
- archive inspection with traversal, symlink, size, encoding, metadata, and collision checks;
- installed skills remain disabled until explicitly enabled;
- exact-version Python and npm CLI installs in isolated managed directories;
- npm lifecycle scripts are disabled unless explicitly confirmed;
- Code Mode exposes a small search-and-execute surface instead of injecting every tool schema.

An installed skill is instruction text, not executable code. An installed CLI is not
automatically callable by a model. That separation is deliberate. MCPO does not yet
provide an operating-system sandbox that can safely run arbitrary generated code.

Do not bridge this gap with `shell=True`, unrestricted Python evaluation, an AST
"sandbox", host-environment `pip install`, or direct execution of archive scripts.

## Target flow

```text
Model drafts package
        |
        v
Read-only inspection and policy lint
        |
        v
Human approves exact archive, package pins, and capabilities
        |
        v
Install disabled + record hashes and provenance
        |
        v
Compile declarative tool manifest
        |
        v
Run in a constrained worker
        |
        v
Register disabled tool -> explicit activation -> Code Mode discovery
```

The model may author and revise a proposal. It must not grant itself capabilities,
activate tools, broaden filesystem access, expose secrets, or enable network access.

## Declarative tool manifest

A proposal archive can include a `TOOL.yaml` that describes one command without
containing executable policy:

```yaml
schemaVersion: 1
name: extract-pdf-tables
description: Extract tables from one supplied PDF into a CSV artifact.
runtime:
  packageId: python-camelot-py-0123456789ab
  executable: camelot
  argv:
    - "{input.pdf}"
    - --format
    - csv
inputSchema:
  type: object
  properties:
    pdf:
      type: string
  required: [pdf]
capabilities:
  network: false
  filesystem:
    read: [input]
    write: [scratch, artifacts]
  environment: []
limits:
  timeoutSeconds: 30
  outputBytes: 65536
activation: disabled
```

Compilation must verify that the package record exists, the executable is one of its
recorded entry points, placeholders match the input schema, arguments are an array,
and every capability is supported by the worker. No shell command string is allowed.

MCPO now exposes read-only preview at `POST /_meta/tool-manifests/preview`. It
reuses the hardened archive limits, requires exact-case `TOOL.yaml`, rejects YAML
aliases, anchors, tags, merge keys, duplicate keys, unknown fields, unbounded input,
network access, environment access, and any activation value except `disabled`.
Schema v1 accepts required string inputs mapped to whole-argument placeholders and
resolves only an exact installed package ID plus exact recorded executable name.

The response binds the uploaded archive, raw manifest, and normalized manifest to
separate SHA-256 hashes. It reports lint validity separately from dependency
resolution and always returns `executionEligible: false`. It does not install,
register, activate, or execute the proposal.

## Execution worker

The execution bridge should be a separate process with a narrow protocol. It needs
enforced, tested controls rather than descriptive flags:

- an empty environment plus explicitly injected variables;
- a fresh scratch directory per call;
- explicit read-only inputs and a separate artifact output directory;
- network disabled by default, with destination allowlists only where enforceable;
- process-tree termination on timeout;
- bounded stdout, stderr, artifact count, artifact size, and total disk use;
- no inherited handles, credentials, repository write access, or MCPO config access;
- audit records containing tool ID, manifest hash, package hash, limits, duration,
  exit status, and artifact hashes, but not secret values.

If these controls cannot be proven on a platform, generated-tool execution stays
disabled on that platform. A working directory is not a filesystem sandbox.

## Context management

Tool creation only helps if discovery does not consume the context window. Keep the
model-facing surface layered:

1. Send a compact capability index with name, one-line description, and tags.
2. Load full schemas only for search matches and the current session working set.
3. Keep skill selection explicit per session. Preserve `[]` as no skills.
4. Store large tool results as artifacts and return a short handle plus summary.
5. Record a prompt hash and selected skill/tool IDs so repeated turns can reuse an
   unchanged prefix.
6. Enforce per-request tool-round, schema-byte, result-byte, and wall-time budgets.
7. Rank search using task text, recent successful tools, and enabled policy, while
   keeping deterministic filters ahead of ranking.

## Delivery sequence

1. Complete: read-only `TOOL.yaml` lint and preview, including manifest and archive hashes.
2. Add package provenance, exact dependency records, and export/import verification.
3. Build the constrained worker and prove denial tests for filesystem, environment,
   network, output, timeout, and child-process escape.
4. Add an approval-backed registry. New and changed tools remain disabled.
5. Feed registry summaries into Code Mode search and lazy-load the selected schema.
6. Add artifact handles, context budgets, and per-tool usage/error telemetry.

The next safe implementation step is package provenance and approval binding. Direct
execution remains blocked until the worker isolation tests exist and pass.
