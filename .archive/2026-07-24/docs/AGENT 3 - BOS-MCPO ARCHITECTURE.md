# AGENT 3 - BOS-MCPO Architecture

Status: memory-derived architecture record

Evidence boundary: this document was written from inherited conversation context, retained memory, and loaded operating guidance only. No repository source, tests, configuration, process state, network state, or runtime endpoints were inspected.

This is a snapshot, not live authority. Verify every current-state claim against source and runtime evidence before implementation or deployment.

Claim labels:

- `[MEMORY]` Retained context states this was designed, implemented, tested, or agreed.
- `[DERIVED]` Architecture inferred from the retained requirements.
- `[UNKNOWN]` Not established without later source or runtime inspection.

1. PURPOSE

- `[MEMORY]` BOS-MCPO joins Building OS operational tools to MCPO's transport, aggregation, authentication, model-routing, and administration surfaces.
- `[MEMORY]` MCPO is useful as a bounded site-facing MCP aggregation and optional provider-routing component.
- `[MEMORY]` MCPO is not the Building OS authority and is not a complete vendor control plane.
- `[MEMORY]` The Building OS gateway remains authoritative for live building, device, space, commissioning, border-router, ledger, and fleet state.
- `[DERIVED]` The combined system should expose Building OS capabilities without moving Building OS ownership into MCPO.

2. NON-NEGOTIABLE INVARIANTS

1. `[MEMORY]` Ports `8001` and `8351` must expose the same MCP servers, tools, filtering, calls, code-mode behaviour, and live toggle state.
2. `[MEMORY]` The only intended difference is authentication and public-edge handling on `8351`.
3. `[MEMORY]` `8351` is the canonical port. Earlier references to `8135` were a typo.
4. `[MEMORY]` REST exposure is independent from MCP server lifecycle.
5. `[MEMORY]` Switching REST off must not stop MCP servers, change server toggles, change tool toggles, or rebuild proxy runtimes.
6. `[MEMORY]` Server and tool toggles must be enforced on discovery and execution, not only represented in the UI.
7. `[MEMORY]` Live state changes must not restart the service graph.
8. `[MEMORY]` Structural configuration changes are the rebuild boundary.
9. `[MEMORY]` Building OS mutation authority stays behind explicit CLI and gateway confirmation gates.
10. `[MEMORY]` The current Building OS MCP core is read-only by design.
11. `[DERIVED]` UI state is never authoritative. Shared server-side state and policy are authoritative.
12. `[DERIVED]` Public credentials, tunnel credentials, private gateway keys, cookies, and proxy headers must never cross trust boundaries accidentally.

3. LOGICAL SYSTEM MAP

```text
External MCP client or ChatGPT
              |
       Shared HTTPS edge
              |
       External OAuth layer
              |
      8351 MCP plus OAuth
              |
   Shared MCPO MCP behaviour
      |                  |
 8001 native MCP     8000 REST, OpenAPI,
 trusted/local       admin and model surfaces
      \                  /
       Shared state and policy
              |
      Shared runtime registry
              |
  Configured MCP servers and adapters
              |
       BOS read-only MCP core
              |
       Building OS gateway
              |
 Spaces, devices, ReCAD, BR, Thread,
 automation, GLDF, fleet and RouterOS
```

`[DERIVED]` A production multi-site form adds a durable vendor control plane and an outbound per-site tooling tunnel. That is a separate layer from the local MCPO listeners.

4. MCPO SURFACES

4.1 Port 8000

- `[MEMORY]` Port `8000` carries REST, OpenAPI, and administration surfaces.
- `[MEMORY]` The model and chat harness is a separate logical plane even if routes share the same application or listener.
- `[MEMORY]` Configured MCP tools can be exposed through REST and aggregate OpenAPI descriptions.
- `[MEMORY]` The REST toggle hides REST/OpenAPI tool exposure and blocks REST tool execution.
- `[MEMORY]` The REST toggle must not stop child servers or alter native MCP availability.
- `[UNKNOWN]` The current authentication policy for every route on `8000`.
- `[UNKNOWN]` The exact current route split between REST, admin, UI, OpenAPI, completion, and model-catalogue functions.

4.2 Port 8001

- `[MEMORY]` Port `8001` is the plain native MCP listener.
- `[MEMORY]` It supports shared server filtering, tool filtering, code mode, live toggles, and stateful sessions.
- `[MEMORY]` It is intended for local or trusted-network use.
- `[MEMORY]` Unsupported roots forwarding was suppressed by using an empty roots set in the remembered implementation.
- `[UNKNOWN]` The current endpoint inventory and any compatibility aliases.

4.3 Port 8351

- `[MEMORY]` Port `8351` is native MCP plus OAuth discovery, authorization, token enforcement, and public URL handling.
- `[MEMORY]` MCP behaviour is shared with `8001`, not independently implemented.
- `[MEMORY]` OAuth control routes must bypass MCP tool filtering.
- `[MEMORY]` Authenticated MCP traffic remains subject to the same server, tool, and code-mode policy as `8001`.
- `[MEMORY]` Missing or invalid credentials must fail authentication before protected MCP behaviour is exposed.
- `[UNKNOWN]` The current public hostname, TLS termination, token state, and live process arguments.

4.4 Behavioural parity

The parity contract is:

```text
servers(8001)     == servers(8351)
tools(8001)       == tools(8351)
filtering(8001)   == filtering(8351)
calls(8001)       == calls(8351)
code_mode(8001)   == code_mode(8351)
toggle_state(8001)== toggle_state(8351)

8351 = 8001 + OAuth and public-edge handling
```

- `[MEMORY]` A previous implementation snapshot reported full parity and regression coverage.
- `[MEMORY]` That snapshot reported `632 passed, 4 skipped`, with narrower parity and lifecycle suites also passing.
- `[UNKNOWN]` Current branch parity. It was deliberately not rechecked for this document.

5. CONFIGURATION, STATE, AND POLICY

5.1 Structural configuration

- `[MEMORY]` `mcpo.json` is the structural server configuration.
- `[MEMORY]` Structural changes may rebuild mounts or runtime topology.
- `[MEMORY]` A statically disabled server is not mounted.
- `[DERIVED]` Structural configuration owns server definitions, command or transport details, and route topology.

5.2 Live state

- `[MEMORY]` `mcpo_state.json` is the shared live state record.
- `[MEMORY]` It carries server toggles, tool toggles, code mode, and REST exposure.
- `[MEMORY]` State changes are refreshed at request time using file-signature change detection.
- `[MEMORY]` State changes must not rebuild mounts or kill child processes.
- `[DERIVED]` Every listener and aggregate route must consume the same state source.

5.3 Toggle truth table

| Control | REST/OpenAPI | 8001 MCP | 8351 MCP | Backend lifecycle |
| --- | --- | --- | --- | --- |
| Server on | Exposed if REST is on | Exposed | Exposed | Available |
| Server off | Hidden and rejected | Hidden and rejected | Hidden and rejected | No toggle-triggered global rebuild |
| Tool on | Exposed if server and REST are on | Exposed if server is on | Exposed if server is on | No lifecycle change |
| Tool off | Hidden and rejected | Hidden and rejected | Hidden and rejected | No lifecycle change |
| REST on | REST tools and schema available | Unchanged | Unchanged | Unchanged |
| REST off | REST tools and schema unavailable | Unchanged | Unchanged | Unchanged |

- `[DERIVED]` A disabled server or tool must fail closed if a client calls a previously cached tool name.
- `[DERIVED]` Hiding a tool from discovery without rejecting direct execution is not a working toggle.
- `[DERIVED]` UI-only filtering is not a security boundary.

6. REQUEST PROCESSING

`[DERIVED]` Shared request flow:

1. The listener accepts a request.
2. `8351` performs OAuth validation when required.
3. Shared live state is refreshed if its signature changed.
4. Server policy is applied.
5. Tool policy is applied.
6. Code-mode policy is applied.
7. The existing MCP runtime handles the allowed request.
8. The response returns through the selected transport.

Required outcomes:

- `[MEMORY]` Unauthenticated public requests return an authentication failure, not policy details.
- `[DERIVED]` Disabled server calls return a stable policy error.
- `[DERIVED]` Disabled tool calls return a stable policy error.
- `[DERIVED]` REST-disabled calls return a REST-exposure error without mutating MCP state.
- `[DERIVED]` Policy changes affect new requests immediately without killing unrelated sessions.

7. RUNTIME LIFECYCLE

- `[MEMORY]` Earlier toggle behaviour rebuilt mounts and caused child-process churn.
- `[MEMORY]` The corrected model separates exposure state from runtime ownership.
- `[MEMORY]` REST changes do not spawn or tear down MCP runtimes.
- `[MEMORY]` Server and tool filtering does not kill unrelated servers.
- `[MEMORY]` Structural config reload is the intended rebuild boundary.
- `[MEMORY]` Duplicate UI handlers previously issued duplicate toggle actions and were removed in the remembered implementation.
- `[UNKNOWN]` Whether any current process duplication remains.
- `[UNKNOWN]` Whether retained sessions or objects currently produce a measurable memory leak.
- `[DERIVED]` Multiple protocol sessions are not automatically duplicate services. A leak claim requires process identity, parentage, session ownership, and retained-memory evidence.

In-flight policy:

- `[DERIVED]` Turning a server or tool off should block new calls immediately.
- `[DERIVED]` In-flight calls should follow a defined drain or cancellation policy.
- `[DERIVED]` A toggle must not kill every configured server.
- `[DERIVED]` Background jobs launched by an async agent server should not be confused with the server process itself.
- `[UNKNOWN]` The current in-flight and background-job policy.

8. BUILDING OS AUTHORITY

- `[MEMORY]` The Building OS gateway owns live building state and operational authority.
- `[MEMORY]` MCPO composes and exposes bounded tools. It does not become the source of truth for spaces, devices, ledgers, commissioning, or fleet state.
- `[MEMORY]` Gateway APIs cover health, spaces, devices, border routers, commissioning, fleet, compilation, and RouterOS-related operations.
- `[MEMORY]` The remembered local gateway default is `http://localhost:7085`.
- `[MEMORY]` Gateway API authentication can use `X-API-Key`.
- `[UNKNOWN]` The current gateway URL, key state, route inventory, and websocket authentication status.

8.1 Building OS mutation boundary

- `[MEMORY]` Building OS write operations stay in CLI scripts and protected gateway routes.
- `[MEMORY]` Confirmation gates protect deployment and commissioning operations.
- `[MEMORY]` The current operating guidance states that BOS MCP write tools are intentionally not registered.
- `[DERIVED]` MCPO must not manufacture an ungated write path around those controls.
- `[DERIVED]` Exposing a CLI agent with bypassed permissions is not equivalent to exposing a bounded Building OS write tool.

8.2 Building OS read-only MCP core

The loaded operating guidance names these read-only operational tools:

- `bos_recad_tbr_artifacts`
- `bos_gateway_health`
- `bos_gateway_inventory`
- `bos_br_verify`
- `bos_br_thread_diagnose`
- `bos_device_probe`
- `bos_br_ledger_commit_diagnosis`
- `bos_convergence_status`

- `[MEMORY]` These tools are diagnostic and verification surfaces.
- `[MEMORY]` Expected hardware degradation must not hide gateway, protocol, or tool failures.
- `[UNKNOWN]` Their current registration and exact schemas.

8.3 Building OS context MCP

The loaded guidance names:

- `bos_context_open`
- `bos_context_search`

Remembered context slices cover:

- automation
- compiler
- agent
- GLDF
- site server
- CLI
- TUI
- ReCAD
- XIAO devices
- mesh
- border router
- agent tooling
- current diary and architecture atlas

- `[MEMORY]` Context MCP is a bounded discovery surface.
- `[MEMORY]` Live source outranks context cache or retained memory.
- `[DERIVED]` Context retrieval and operational control should remain separate toolsets.
- `[UNKNOWN]` Current slice inventory and cache behaviour.

9. BOS DOMAIN FLOWS

9.1 Automation and ledger flow

`[MEMORY]` The intended workflow is:

```text
author automation
      |
validate source
      |
compile ledger
      |
inspect evidence
      |
explicit confirmation
      |
gateway deployment
      |
convergence verification
```

- `[MEMORY]` ReCAD and ledger deployment are protected write paths.
- `[DERIVED]` MCP can support read-only preparation and diagnosis without bypassing the confirmation boundary.

9.2 Commissioning flow

- `[MEMORY]` Commissioning includes discovery, physical identification, space pairing, identity imprint, and commissioning-ledger handling.
- `[MEMORY]` Commissioning is safety-sensitive and must preserve explicit operator boundaries.
- `[DERIVED]` Public or autonomous MCP clients should receive diagnostic tools by default, not unrestricted commissioning mutations.

9.3 Border router and Thread flow

- `[MEMORY]` BOS diagnostics cover border-router health, Thread join state, commissioner state, joiner credentials, steering data, radio proof, and RCP recovery evidence.
- `[DERIVED]` Tool results should distinguish building-hardware offline conditions from gateway and protocol defects.

9.4 GLDF and device flow

- `[MEMORY]` GLDF packages, device firmware, hardware declarations, card installation, and firmware provenance are connected operational concerns.
- `[DERIVED]` MCPO may expose bounded inventory, verification, and diagnostic functions while release, flash, and deployment remain gated.

9.5 RouterOS and site networking

- `[MEMORY]` Building OS includes RouterOS, WireGuard, PoE, route-state, and site-network diagnostic workflows.
- `[DERIVED]` Network mutation tools require separate authorization and confirmation from general MCP read access.

10. CONFIGURED OR REQUESTED MCP SERVER INVENTORY

This is intent remembered from the conversation, not a verified installation list.

10.1 Text editor server

- `[MEMORY]` Requested server: `mcp-text-editor`.
- `[MEMORY]` Purpose: token-efficient line-range reads, hash-checked patches, multi-file operations, encoding support, and concurrent-edit detection.
- `[DERIVED]` It should be filtered by MCPO server and tool toggles like every other server.
- `[DERIVED]` Hash checks reduce edit races but do not create an operating-system security boundary.

10.2 Claude Code one-shot server

- `[MEMORY]` Requested server: `@steipete/claude-code-mcp`.
- `[MEMORY]` Purpose: delegate one prompt to a separate Claude Code process.
- `[MEMORY]` Permission bypass was intentional for this one-shot task surface.
- `[DERIVED]` It must be treated as a high-trust execution server.
- `[DERIVED]` MCPO can block access to the MCP tool, but cannot constrain shell operations after an unrestricted child agent has been launched.

10.3 Codex as MCP

- `[MEMORY]` Requested server: `codex-as-mcp`.
- `[MEMORY]` Tools include single-agent and parallel-agent delegation.
- `[MEMORY]` It invokes Codex with approvals and sandbox bypassed.
- `[DERIVED]` Server and tool toggles can prevent new delegations.
- `[DERIVED]` Hard filesystem limits require a container, VM, restricted account, or equivalent OS boundary.

10.4 AI CLI MCP

- `[MEMORY]` Requested server: `ai-cli-mcp`.
- `[MEMORY]` It delegates background work to Claude, Codex, Gemini, Forge, and OpenCode.
- `[MEMORY]` It manages process IDs, waiting, peeking, results, and termination.
- `[MEMORY]` Dangerous approval bypass was explicitly accepted for this server.
- `[DERIVED]` The server toggle controls new tool access. It must not silently kill all existing jobs.
- `[DERIVED]` Existing background jobs require an explicit lifecycle policy and a specific kill operation.

10.5 GitHub MCP

- `[MEMORY]` Requested server: the official GitHub MCP Server.
- `[MEMORY]` It supports remote OAuth or PAT access, local Docker operation, toolsets, individual tool allowlists, read-only mode, and lockdown mode.
- `[DERIVED]` Use read-only mode when mutation is not required.
- `[DERIVED]` Omit concrete destructive tools such as `delete_file` from individual tool allowlists.
- `[DERIVED]` MCPO should also hide and reject any disabled GitHub tool for defence in depth.

10.6 DuckDuckGo MCP

- `[MEMORY]` Requested server: `duckduckgo-mcp-server`.
- `[DERIVED]` It belongs in the untrusted external-content class.
- `[DERIVED]` Search output must never be treated as trusted operating instruction.
- `[UNKNOWN]` Exact package, command, tool names, and current installation state.

11. DELETION CONTROL

- `[DERIVED]` There is no universal MCP `/delete` method to block.
- `[DERIVED]` Deletion must be controlled at concrete tool and execution boundaries.

For bounded MCP servers:

1. Do not register destructive tools.
2. Use server-provided read-only mode when available.
3. Use an explicit tool allowlist.
4. Hide destructive tools from discovery.
5. Reject direct calls to disabled destructive tools.
6. Restrict credentials so the upstream API cannot delete.
7. Log denied attempts.

For GitHub MCP:

- `[DERIVED]` Read-only mode is the strongest built-in general control.
- `[DERIVED]` A tool allowlist can omit `delete_file` and other write tools.
- `[DERIVED]` A read-only or least-privilege token provides another enforcement layer.

For delegated CLI agents:

- `[MEMORY]` Claude Code MCP, Codex-as-MCP, and AI CLI MCP were intentionally requested with approval or sandbox bypass.
- `[DERIVED]` Tool-name filtering only prevents launching the agent. It cannot guarantee that a launched unrestricted agent will not delete files.
- `[DERIVED]` A hard no-delete guarantee requires filesystem permissions, a read-only mount, container isolation, a VM, or a restricted service account.
- `[DERIVED]` Prompt instructions alone are not a security control.

12. LOCAL AUTHENTICATION BOUNDARIES

- `[MEMORY]` Local `http://localhost:8001/mcp/` and remote OAuth-protected MCP are separate services and must not be conflated.
- `[MEMORY]` MCP health is proven with an authenticated JSON-RPC `initialize` request, not a browser GET.
- `[MEMORY]` A `401` indicates authentication failure.
- `[MEMORY]` A `405` indicates method mismatch and is a different fault.
- `[MEMORY]` Trailing-slash behaviour affected authentication in a previous machine-local setup.
- `[MEMORY]` Literal bearer configuration was once required because a parent process did not reliably inherit an environment variable.
- `[UNKNOWN]` That machine-local configuration may now be stale.
- `[DERIVED]` No credential value belongs in this document, committed configuration, logs, or diagnostic output.

13. MULTI-SITE VENDOR ARCHITECTURE

13.1 Current MCPO capability boundary

- `[MEMORY]` MCPO can aggregate configured MCP backends, expose Streamable HTTP, provide OAuth experiments, register providers, catalogue models, and forward OpenAI-compatible completions.
- `[MEMORY]` Its remembered persistence is mainly JSON and configuration state with in-memory runtime connections and metrics.
- `[MEMORY]` That is not a durable multi-tenant site database.
- `[MEMORY]` That is not a durable tunnel queue.
- `[MEMORY]` A normal MCPO process is one application listener set with backend mounts inside it.
- `[DERIVED]` Per-site isolation requires orchestration outside the current core.

13.2 Required vendor services

`[DERIVED]` A production vendor control plane needs:

- customer identity and tenancy
- site and gateway ownership
- device and gateway enrollment
- OAuth clients, grants, scopes, revocation, and rotation
- per-site credentials and secrets
- durable request queues
- tunnel leases and single-active-client enforcement
- request correlation and deadlines
- retry and idempotency policy
- durable audit
- rate limiting and abuse controls
- operations and fleet administration
- database migrations and backup
- health, metrics, traces, and alerting
- horizontal scaling and high availability
- optional model-provider routing
- optional managed inference as a separate service

13.3 Reference deployment

This is a derived design, not a confirmed implementation:

```text
Public client
    |
Shared HTTPS edge
    |
External OAuth and policy
    |
Vendor control plane
    |-----------------------|
    |                       |
Site directory         Optional model service
    |
Durable tooling queue and lease service
    |
Outbound site tunnel
    |
Per-site MCPO instance
    |
BOS read-only MCP adapter
    |
Building OS gateway
    |
Local building systems
```

- `[MEMORY]` The stronger boundary is per-site or site-pinned MCPO composition, not one shared unrestricted global tool graph.
- `[MEMORY]` Customer sites should not require a public inbound port.
- `[MEMORY]` The site connector should establish an outbound connection.
- `[DERIVED]` Central and per-site components must use stable site identifiers, not raw port numbers as identity.

14. TUNNEL CONTRACT

- `[MEMORY]` The reviewed OpenAI tunnel client is gateway-side transport plus a published protocol.
- `[MEMORY]` It is not the production vendor backend.
- `[MEMORY]` Remembered protocol routes include tunnel state, polling, and response endpoints.
- `[MEMORY]` Three endpoint paths are not the whole protocol.
- `[MEMORY]` Headers, envelopes, correlation, deadlines, retries, leases, streaming, termination, and failure semantics are load-bearing.

Required security behaviour:

1. `[MEMORY]` Strip public authorization headers.
2. `[MEMORY]` Strip cookies and proxy headers.
3. `[MEMORY]` Inject the private local MCP key only after crossing into the trusted site boundary.
4. `[MEMORY]` Bound request, poll, and response sizes.
5. `[MEMORY]` Enforce deadlines.
6. `[MEMORY]` Prevent multiple active clients from owning one tunnel.
7. `[MEMORY]` Recover requests that were never dispatched.
8. `[MEMORY]` Mark dispatched requests with unknown outcome as unknown.
9. `[MEMORY]` Never automatically replay an uncertain mutation.

Tunnel exclusions:

- `[MEMORY]` Pages do not cross the tooling tunnel.
- `[MEMORY]` HTML does not cross the tooling tunnel.
- `[MEMORY]` JavaScript and assets do not cross the tooling tunnel.
- `[MEMORY]` Cookies do not cross the tooling tunnel.
- `[MEMORY]` Model traffic does not cross the tooling tunnel.
- `[MEMORY]` The relay is MCP-tool transport, not an arbitrary path, GET, page, or browser-session proxy.

15. SERVICE PLANES THAT MUST STAY SEPARATE

1. REST and OpenAPI tool exposure.
2. Native MCP transport.
3. OAuth and public authentication.
4. Model and chat harness.
5. Administration and UI.
6. Tooling tunnel.
7. Vendor identity and site control plane.
8. Building OS gateway authority.
9. Optional inference hosting.
10. Pages and browser-session delivery.

- `[MEMORY]` Collapsing these into one generic proxy hides security and lifecycle boundaries.
- `[DERIVED]` They may share processes during development, but their contracts and authority must remain separate.

16. FAILURE CLASSIFICATION

Every failure should be classified before recovery:

- listener unavailable
- OAuth discovery unavailable
- authentication rejected
- HTTP method mismatch
- MCP initialize or protocol failure
- server disabled by policy
- tool disabled by policy
- REST disabled by policy
- structural configuration invalid
- backend MCP server unavailable
- gateway unavailable
- Building OS hardware offline
- tunnel disconnected
- tunnel lease lost
- deadline exceeded
- uncertain remote mutation
- model provider unavailable
- UI stale or duplicate action
- process churn
- confirmed retained-memory growth

- `[MEMORY]` Expected BR or device degradation must not hide a real gateway, protocol, or tool error.
- `[DERIVED]` Restarting every server is not a valid default response to a policy toggle.
- `[DERIVED]` Uncertain writes must remain unknown until reconciled.

17. OBSERVABILITY

`[DERIVED]` Minimum operational signals:

- listener health for `8000`, `8001`, and `8351`
- parity comparison between `8001` and `8351`
- configured server count
- mounted server count
- enabled server and tool counts
- active MCP session count
- backend child-process count and parent ownership
- state signature and refresh count
- structural config reload count
- REST exposure state
- toggle rejection count
- OAuth rejection count
- schema generation and invalidation count
- background agent PID and status
- per-site tunnel state and lease owner
- queue depth and oldest request age
- deadline and retry counts
- unknown-outcome request count
- gateway health and convergence state
- memory and process trends across repeated toggles

`[DERIVED]` Logs must redact bearer tokens, OAuth credentials, tunnel credentials, API keys, cookies, and sensitive prompt content.

18. KNOWN NON-GOALS

- `[MEMORY]` MCPO JSON state is not a production tenancy database.
- `[MEMORY]` The tunnel client is not the production vendor service.
- `[MEMORY]` Model forwarding is not proof of hosted inference.
- `[MEMORY]` An MCP endpoint is not a page or browser-session tunnel.
- `[MEMORY]` Public OAuth does not replace Building OS confirmation gates.
- `[DERIVED]` The admin UI does not own runtime truth.
- `[DERIVED]` A tool toggle does not replace upstream least-privilege credentials.
- `[DERIVED]` A bypassed CLI agent is not a bounded domain API.

19. VERIFICATION QUEUE

The following remain unknown because code and runtime inspection were explicitly excluded:

1. Current branch and worktree state.
2. Current listener processes on `8000`, `8001`, and `8351`.
3. Current endpoint inventory.
4. Current server and tool inventory.
5. Current `mcpo.json` structure.
6. Current `mcpo_state.json` structure and values.
7. Current 8001 and 8351 parity.
8. Current REST toggle behaviour.
9. Current server and tool filtering behaviour.
10. Current in-flight call policy.
11. Current child-process ownership.
12. Any current memory leak.
13. Current OAuth, TLS, and public hostname configuration.
14. Current container and supervisor topology.
15. Current Building OS gateway routes and authentication.
16. Current BOS MCP tool registration.
17. Current tunnel implementation and vendor backend.
18. Current database, queue, lease, audit, and HA services.
19. Real public Cloudflare and ChatGPT end-to-end behaviour.
20. Current deletion controls for each configured server.

20. ARCHITECTURAL DECISION RECORD

The retained architecture resolves to these decisions:

1. Use MCPO as bounded MCP aggregation, transport adaptation, optional OAuth, and optional provider routing.
2. Keep Building OS gateway authority intact.
3. Keep BOS operational MCP read-only.
4. Keep mutations behind Building OS CLI and explicit gateway confirmation.
5. Make `8001` and `8351` behavioural twins.
6. Add only OAuth and public-edge handling on `8351`.
7. Treat REST exposure as policy, not lifecycle.
8. Enforce server and tool toggles on discovery and execution.
9. Apply state toggles live without rebuilding runtimes.
10. Rebuild only for structural configuration changes.
11. Isolate sites with per-site MCPO or an equivalent hard orchestration boundary.
12. Put durable tenancy, queueing, leases, audit, and HA in a vendor control plane outside MCPO's JSON state.
13. Use an outbound tool-only site tunnel.
14. Keep pages, cookies, assets, and model traffic outside that tunnel.
15. Treat unrestricted delegated CLI agents as high-trust execution, not safe bounded tools.
16. Use OS-level isolation when deletion must be impossible.

21. FINAL MEMORY POSITION

- `[MEMORY]` A previous snapshot reported that the parity and toggle defects were fixed and tested.
- `[UNKNOWN]` That result is not asserted as current because this document deliberately did not inspect the repository or runtime.
- `[MEMORY]` The wider BOS vendor architecture was assessed as partially designed, with MCPO suitable as a component but not as the whole service.
- `[DERIVED]` The durable architecture is a BOS-authoritative, site-isolated, policy-driven tool plane with MCPO at the transport boundary and a separate production vendor control plane.
