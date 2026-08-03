# BOS Vendor Connector — Canonical Architecture

**Status:** canonical. Supersedes the four `agent{1,2,3,4}` drafts (archived to
`.archive/2026-07-24/docs/` on 2026-07-24).
**Authoritative sources:** `docs/bos-tunnel-doc` (source-cited audit, retained) and the
`.claude/diary-*.md` records. The four agent drafts were memory-derived narrative and are
not evidence.
**Labels:** [SETTLED] design decided · [LIVE] verified running 2026-07-24 · [OPEN] genuine
undecided/unwired · [UNVERIFIED] claimed but not confirmed.

---

## 1. Core principle — site_id is the routing key at every hop [SETTLED]

Every hop for a customer uses that customer's **site_id** (e.g. `44354`) as the port /
route. It does two jobs:

1. **Multi-tenant routing key.** One number identifies the site end to end. A fixed shared
   port could only ever serve one customer.
2. **Fail-closed misrouting guard.** A request pinned to site N can only reach the server
   listening on port N. A cross-site mismatch **fails closed** (wrong port = nothing
   listening); it can never silently proxy site A's tools to site B's server.

> The old fixed client port `7087` is **BINNED** — replaced by `127.0.0.1:<site_id>` (a fixed
> port breaks both properties above). `7085` remains the client **gateway**. `bos-tunnel-doc`
> still references 7087 (~line 118) — stale. NOTE: the connector code
> (`vendor-relay/config.ts`, `BOS_CHATGPT_MCP_PORT`) still hardcodes 7087 → code lags the
> design. Do not use 7087.

## 2. Direction — who dials whom [SETTLED]

- The **client gateway dials OUT** to BOS and keeps an outbound session open.
- **BOS never dials into the client.** The client exposes **no public inbound port**; the
  customer network needs no inbound firewall rule.
- Only the **BOS side listens publicly** (Cloudflare, 443).

## 2b. Two public entrypoints [owner-stated design; `connect` not live]

BOS owns the `ai.lighting` **vendor add-on** — the sellable product. It exposes two public
entrypoints, one per direction:

1. **`<site_id>.ai.lighting`** (e.g. `44354.ai.lighting`) — where a client's **ChatGPT / MCP
   client** connects IN. OAuth-gated → mcpo container. [LIVE]
2. **`connect.ai.lighting`** — the **single shared entrypoint** every client gateway's
   outbound (OpenAI) tunnel dials OUT to. The **site_id** on each incoming connection
   resolves it to the correct client; routed over HTTP to the mcpo container. [not live —
   DNS 502]

The mcpo container ties the two: ChatGPT requests on `<site_id>.ai.lighting` are served using
the client tools hanging off that site's tunnel landed at `connect.ai.lighting`. Same
`site_id` on both sides; a mismatch fails closed.

The vendor add-on holds a live `site_id → client-tunnel` map and enforces **one active client
per site** — **code-confirmed built** (§9), not planned. Caveat: that broker state is
single-process **in-memory**, so a restart drops live sessions.

## 3. Request path — current live design [LIVE for the front, OPEN below it]

```
ChatGPT / MCP client  (the CLIENT's own harness, site 44354)
  → https://44354.ai.lighting/mcp          BOS public, OAuth-gated      [site_id = 44354]
  → BOS Site Connector (MCPO container)     host 127.0.0.1:44354 → container :8351
  → BOS vendor add-on = tunnel relay        (connect.ai.lighting) — bridges relay ↔ client, pins site 44354
  → the client's already-open OUTBOUND tunnel
  → client 127.0.0.1:44354/mcp              (localhost:site_id — THIS serves the tools)
  → tools call → BOS API set                (BOS's real capabilities)  [PLANNED]

  Every hop = 44354.  A mismatch anywhere = hard fail, never wrong-site.
```

The vendor add-on terminates the public site access key and **never forwards it**; the
**client** injects its own private local MCP key before hitting its local tools. The tools
are callers, not the logic — they invoke **BOS's API set** (BOS owns the real capabilities).
BOS's full API set is [PLANNED].

## 4. Hostname map

| Host | Role | Side | Status |
|---|---|---|---|
| `44354.ai.lighting` | Public client-facing MCP endpoint, OAuth-gated (`site_id.ai.lighting`) | BOS | [LIVE] built 2026-07-24, free `*.ai.lighting` cert |
| `dev.ai.lighting` | SEPARATE general/dev tooling MCPO (ChatGPT/Claude/Codex dev). Not part of the site path | BOS | [LIVE] |
| `44354.gateway.ai.lighting` | Abandoned earlier front-door name. Two-level, not covered by the free cert. Dropped for `44354.ai.lighting` | BOS | dead (orphan CNAME may linger) |
| `connect.ai.lighting` | **Proposed value** for the vendor add-on's tunnel entrypoint — the host is env-driven (`BOS_VENDOR_RELAY_URL` / `VENDOR_PUBLIC_DOMAIN`), NOT hardcoded. The relay endpoint itself is code-confirmed built (§9). This DNS name exists but returns **502** — not live | BOS | relay BUILT (§9); this hostname not yet live |

## 5. Ports

- **Client side (per site):** connector → `127.0.0.1:<site_id>/mcp` (the tool server —
  **replaced the old fixed `7087`, now binned**) → `7085` (the client **gateway**, remains) →
  tools. `7087` = binned, do not use.
- **BOS side:** public `443` (Cloudflare) → host `127.0.0.1:<site_id>` → MCPO container
  internal `:8351` (fixed). Vendor loopbacks `7089` (admin API) and `7088` (bench testing)
  are **BOS-side, not client-side**.
- `8000` admin UI / `_meta` REST and `8001` plain native MCP are **local-only** dev ports.

## 6. Live state — 2026-07-24 [LIVE, verified this session]

- `https://44354.ai.lighting/.well-known/oauth-authorization-server` → **200**, issuer
  `https://44354.ai.lighting/`, authorize/token/register correct. `/mcp` → **401** (gate
  active). TLS via the free `*.ai.lighting` cert.
- Tunnel: cloudflared **CLI-managed** tunnel `bos-gateway`
  (`67406432-2e94-45bb-82c5-d86d21158baa`), config at
  `C:\Users\danie\.cloudflared\bos-gateway.yml`. Runs as a **background process, not a
  service** — dies on reboot.
- The `dev.ai.lighting` token tunnel was left untouched.
- Container `mcpo-site-44354-gateway-mcpo-1` (Compose project `mcpo-site-44354`), healthy,
  `--public-url https://44354.ai.lighting`, resource
  limits (1 CPU / 512m / pids 256) active after the recreate.

## 6b. MCP spec 2026-07-28 — impact on this architecture [grounded 2026-07-30]

The MCP 2026-07-28 revision (published 2026-07-28) removes protocol sessions
(`Mcp-Session-Id` gone from Streamable HTTP), removes the initialize handshake
(per-request `_meta` versioning + `server/discover`), replaces server-initiated
requests with client-retried MRTR, and adds `Mcp-Method`/`Mcp-Name` routing headers.
WHY it matters here: the per-site mcpo container and the add-on relay can become
plain stateless request/response hops — no session affinity, no sticky broker state
for the MCP layer itself (the broker's site→tunnel map remains the one stateful hop).

Implemented in mcpo (2026-07-30, middleware-level, works in front of old-SDK backends):
`server/discover` answered by the proxy, `Mcp-Method`/`Mcp-Name` validation (-32020),
`_meta` protocolVersion check (-32022), deterministic + cacheable `tools/list`
(ttlMs/cacheScope), `--stateless-http` mode now keeps the roots=[] suppression, admin
remounts honor the configured stateless flag.

UPDATE same day [grounded 2026-07-30, owner-directed]: upgraded to mcp 2.0.0 +
fastmcp 4.0.0b1 after an isolated worktree trial passed (3 renames, 679/679 tests,
live smoke incl. a sessionless 2026-07-28 request through to a stdio backend; old
2025-06-18 clients still work via era mirroring; proxy bug #3169 fixed upstream in
this beta). WHY not wait for GA: trial evidence beat the calendar; pin is exact
(`fastmcp==4.0.0b1`) so nothing drifts. Bump to 4.0 GA when it ships. Known ecosystem
fallout independent of us: unpinned uvx Python MCP servers importing `McpError`
(e.g. mcp-server-time) crash since mcp 2.0 hit PyPI — needs per-server pins in
mcpo.json, decision open. Upstream open-webui/mcpo has adopted none of this
(idle since 2026-02).

## 7. Open decisions — NOT settled, do not treat as resolved [OPEN]

1. **Front door / roles — SETTLED (owner decision, 2026-07-24; see §10).** The **add-on is
   the front-door manager** (TLS, encrypted access, health, fan-out, tunnel control); **mcpo
   is the per-site plugin behind it** (ChatGPT OAuth + proxy-out + logs + sandbox). WHY: all
   traffic routes THROUGH the add-on so it stays in the control loop; deliver the tunnel
   straight to mcpo and the add-on's access/health/fan-out is bypassed; containers stay
   sandboxed and never directly exposed. This supersedes the 2026-07-23 report's "mcpo in
   front." Implementation path (worked at build time, not a design fork): mcpo does OAuth, then
   proxies out to the add-on's per-site endpoint, which dispatches over the client tunnel.
2. **Backend wiring / the hairpin.** The container's backend is currently
   `https://44354.ai.lighting/mcp` — i.e. it points at **itself**. This must be re-pointed
   at the real vendor broker / customer route. The **customer gateway is currently OFF**,
   so tool calls do not work yet (login/discovery do).
3. **Transport.** BOTH are BUILT (§9): **WSS relay** (`/relay/connect`, the working one,
   usable now) and **OpenAI HTTP long-poll** (`/v1/tunnels/{id}/poll`). The OpenAI
   `tunnel-client.exe` **IS installed** and passes its hash+version gates (verified
   2026-07-24) — NOT the blocker. That path is inert only because `connect.ai.lighting` is
   DNS-502 and the vendor must set `VENDOR_SITE_TRANSPORT=openai-poll`.
4. **`connect.ai.lighting`** — a proposed hostname for the add-on's tunnel entrypoint (env-
   driven, not in code — §9). site_id demux + one-per-site are code-confirmed. DNS exists yet
   returns 502; the hostname is not yet live/wired (see §4/§9).
5. **Tunnel durability** — make `bos-gateway` auto-start (service/scheduled task). Not done.

## 8. Superseded / retained

- **Archived** to `.archive/2026-07-24/docs/`: `agent1-bos-mcpo-architecture.md`,
  `agent 2 - bos-mcpo-architecture.md`, `AGENT 3 - BOS-MCPO ARCHITECTURE.md`,
  `agent 4 -bos architecture.md`. Reason: duplicate/conflicting memory-derived drafts (two
  were both "Agent 2"), drifting test counts (632/640/667), and the stale `7087`.
- **Retained:** `docs/bos-tunnel-doc` (the cited source for the verified original relay)
  and `docs/VENDOR_CONNECTOR_CLIENT_GATEWAY_VM_POC.md` (the separate client-VM POC plan).

## 9. Vendor add-on — tunnel endpoint [CODE-CONFIRMED 2026-07-24]

Read directly from `C:\Users\danie\building-os\tools\bos-vendor-addon`. The outbound-tunnel
**vendor endpoint is BUILT** (not a stub). It lives in the vendor add-on, **not** in mcpo.

- **Two transports, selected by env `VENDOR_SITE_TRANSPORT`** (default `websocket`):
  - **WSS relay** — `GET /relay/connect` (subprotocol `bos.relay.v1`). The working one.
  - **OpenAI HTTP long-poll** — `GET /v1/tunnels/{id}/poll` + `POST /v1/tunnels/{id}/response`.
    Built. The `openai-tunnel-client` binary **IS installed**
    (`tools/openai-tunnel-client/bin/windows_amd64/tunnel-client.exe`; sha256 + `--version`
    verified 2026-07-24). Inert only until `connect.ai.lighting` is live and the vendor sets
    `VENDOR_SITE_TRANSPORT=openai-poll`.
- **site_id demux + one-active-client-per-site: IMPLEMENTED.** Relay keys sessions by siteId
  and rejects a 2nd connector (`site_already_connected`, code 4409); the OpenAI broker throws
  `poll_in_progress` 409. Per-site public listener binds `Number(site_id)` (range
  10000–65535). site_id IS the port — no separate mapping table.
- **`connect.ai.lighting` is NOT in the code.** The entrypoint host is env-driven: the client
  sets `BOS_VENDOR_RELAY_URL` (path must be `/relay/connect`); the vendor builds its public
  site URL from `VENDOR_PUBLIC_DOMAIN`. So `connect.ai.lighting` is a **value you configure**,
  not code to write.
- **Vendor env vars:** `VENDOR_SITE_TRANSPORT`, `VENDOR_ADMIN_API_KEY`, `VENDOR_DB_PATH`,
  `VENDOR_PUBLIC_DOMAIN`, `VENDOR_HOST/PORT`, `VENDOR_ADMIN_HOST/PORT`,
  `VENDOR_SITE_LISTENER_HOST`, `VENDOR_TLS_CERT_PATH/KEY_PATH/CERT_DIR`, `VENDOR_INSECURE_HTTP`.
  Site IDs are allocated/stored in SQLite, not an env var.
- **Client env vars** (`host/runtime/server/src/vendor-relay/config.ts`): `BOS_VENDOR_RELAY_URL`,
  `BOS_VENDOR_CONNECTOR_KEY`, `BOS_VENDOR_SITE_ID`, `BOS_CHATGPT_MCP_API_KEY`,
  `BOS_CHATGPT_MCP_PORT` (7087), plus `BOS_VENDOR_ENROLLMENT_TOKEN` at enrollment.
- **Ports (add-on):** public/control `443` (or `7088` loopback insecure bench); admin `7089`
  loopback; per-site listeners on `Number(site_id)`; consumer core FastMCP `7087`.
- **Caveat:** broker state is single-process **in-memory** — a restart drops live sessions.

Composition (checked 2026-07-24): as WIRED today it conflicts — mcpo and the add-on both
claim `<site_id>.ai.lighting` + the same loopback port, and mcpo's backend hairpins to
itself. RESOLUTION (owner intent, §7.1/§10): add-on is the front-door manager, mcpo is the
per-site plugin behind it, run on a **shared docker network** so mcpo→add-on is direct
(`http://vendor-addon:<port>`), which removes the hairpin and the host collision.

## 10. Component roles, auth boundaries, and the "why" [owner-stated 2026-07-24]

Kept current as detail settles. Records not just WHAT but WHY it must be this way.

### Roles
- **Vendor add-on = the front-door MANAGER.** Owns public TLS / encrypted access, health
  monitoring, fan-out to per-site containers, and the tunnel control plane
  (`connect.ai.lighting`). WHY: it is BOS's client-tool manager — all traffic routes through
  it so it stays in the control loop. Bypass it (tunnel straight to a container) and there is
  no central access control, health, or managed fan-out.
- **mcpo = the per-site PLUGIN** (one container per site, sandboxed). Owns the ChatGPT OAuth,
  proxy-out to the site_id endpoint (from its volume-mounted config), log capture, and a
  sandbox to add other tools or, later, own-inference via the openapi proxy. WHY: mcpo is what
  ChatGPT connects to as a plugin, so the OAuth lives here; the container is the per-tenant
  sandbox plus a debuggable, extensible surface.

### Three separate auth boundaries (NOT one decision — each owned by the layer that faces it)
1. **Client's tool keys — issued by the CLIENT.** The tools MCP server belongs to the client,
   so the keys to its tools are the client's. BOS **never holds them** (bos-tunnel-doc: the
   client injects "its OWN private local MCP key"). The connector presents that client-owned
   key to the local tool server. BOS controls the tunnel, NOT the tools.
2. **Tunnel token — issued + proven (validated) by the vendor add-on.** It opens the tunnel
   between the client's MCP server and the add-on (enrollment/connector token, `/enrollment-tokens`).
   This is what BOS controls: TUNNEL access, not tool access.
3. **ChatGPT OAuth — a login / consent screen** that pops up when the plugin (mcpo) connects;
   ChatGPT authenticates there. Lives at mcpo (the plugin source); the add-on never does it.

### The OpenAI tunnel — self-hosted, NOT a SaaS dependency
- `openai/tunnel-client` is a standalone Apache-2.0 binary. Purpose (README): connects a
  private/localhost MCP server to ChatGPT/Codex/etc. through an MCP tunnel endpoint, keeping
  the server off the public internet. Transport = outbound HTTPS long-poll
  (`/v1/tunnels/{id}/poll` + `/response`).
- You RUN the client yourself and point it at YOUR control plane (`connect.ai.lighting` = the
  add-on's `/v1/tunnels` routes), NOT OpenAI's cloud. WHY: reuse the open client, self-host
  the endpoint → off OpenAI SaaS. The add-on's own ACME/TLS also makes Cloudflare replaceable.
- `connect.ai.lighting` is ONE endpoint, not one-per-site: the tunnel_id/site_id is in the URL
  path, so a single control plane demuxes all sites. site_id is the routing key at every hop.

### Deployment shape (why containers + shared network)
- **One mcpo container per site = the sandbox boundary** for the sensitive per-tenant thing
  (the tools). The relay/control plane is thin (no tools, no data) and shared. WHY: isolate
  what matters; a shared thin relay is low-risk.
- **mcpo + add-on on a shared docker network** → container-to-container by service name. WHY:
  removes the host-loopback hairpin, keeps add-on site listeners off host loopback (smaller
  surface), and needs NO docker socket / docker CLI.
- **Client dials OUT only** (no inbound port) → NOT two tunnels per client; vendor endpoints
  are shared (`<site>.ai.lighting` + `connect.ai.lighting`), demuxed by site_id.

## 11. Change-list to reach target [grounded audit 2026-07-24]

Both codebases audited against the settled target. Summary + decision-gates here; full
file:line lists were captured in the 2026-07-24 audits.

### Client gateway (`host/runtime` + `vendor-relay`) — a bounded refactor
- **`7087` is BINNED → tool server moves to `127.0.0.1:<site_id>`.** Today it's two-tier: a
  site_id facade (`site-listener.ts`) forwards to a private FastMCP "core" hard-bound on 7087
  (`chatgpt_mcp/config.py:15`; hard guards `config.py:74-83,160-165` and `config.ts:70-82`
  **reject** any non-7087 value, so it can't move without code change). [CHANGE]
- **KEEP:** the WSS connector (`connector.ts`, real+tested), FastMCP logic/key/gateway, site
  gateway `7085`, the client-side local key (`BOS_CHATGPT_MCP_API_KEY`, DPAPI-stored,
  client-minted — §10 boundary 1), enrollment/connectorKey.
- **Transport:** WSS is fully wired + usable now. The OpenAI `tunnel-client.exe` IS installed
  and verified — the OpenAI path is inert only because `connect.ai.lighting` is 502 and the
  vendor must set `VENDOR_SITE_TRANSPORT=openai-poll`. [infra/vendor, not client code]
- **Go-live config:** `BOS_VENDOR_RELAY_URL=wss://connect.ai.lighting/relay/connect` (path
  must be exactly `/relay/connect`); CONTROL_PLANE_* are derived, not separately set.

### Vendor add-on — the "manager" role is largely NEW
- It's a bare node relay: **no Dockerfile, no compose, no mcpo, no reverse-proxy, no container
  health.** It serves `/mcp` itself and dispatches to the client WSS tunnel.
- **KEEP:** site access keys, enrollment tokens, connectorKey + one-per-site demux, site_id
  demux (10000-65535), per-site listeners (mechanism), ACME/TLS scaffolding. OAuth stays OUT
  (lives in mcpo).
- **NEW (gated on ordering decision):** fan-out to per-site mcpo containers, a public
  front-router (`@fastify/http-proxy` is an *unused* devDependency), an outbound-proxy path,
  per-container health, and the whole docker/compose/shared-network layer.
- **CHANGE:** per-site listeners bind loopback only (`site-listener-manager.ts:57-62`) —
  blocks container-to-container by service name; cloudflare-route rows point at loopback.
- **TLS limit:** the add-on's own ACME is single-CN (`tls.ts:404`) — covers
  `connect.ai.lighting` only, not `*.ai.lighting`. Cloudflare does the wildcard today.

### Rename (owner requirement)
- **`chatgpt` → `gateway_tools_mcp` in the hardcoded tools** — bounded: the
  `host/runtime/chatgpt_mcp` module + `BOS_CHATGPT_MCP_*` env vars + tests. [CHANGE]
- **"avoid bos"** — SCOPE UNDECIDED: just the tool-facing names, or the whole `BOS_` prefix
  across both repos (large; breaks current enrollments until migrated). [DECISION]

### Build notes under the settled design (resolved / implementation detail — NOT open decisions)
1. **Request ordering — SETTLED: add-on is the front-door manager; mcpo is the per-site plugin
   behind it** (§7.1/§10). The add-on's manager / fan-out / health / router work is the NEW
   build listed above.
2. **Client tool server — SETTLED:** FastMCP binds `127.0.0.1:<site_id>` directly; the private
   `7087` core hop is collapsed/removed (7087 binned).
3. **Launch ordering (implementation):** the launcher learns `site_id` from the committed
   connector credential before starting the tool server.
4. **TLS (implementation, pick at build time):** extend the add-on's ACME to a wildcard/SAN
   cert for `<site>.ai.lighting`, or keep Cloudflare's `*.ai.lighting` wildcard.
5. **Transport — WSS ships now** (only live path; `connect.ai.lighting` is 502). OpenAI-poll
   switches on once the control plane is live and the vendor sets
   `VENDOR_SITE_TRANSPORT=openai-poll`.
6. **Rename:** `chatgpt` → `gateway_tools_mcp` in the tools; avoid `bos` in tool-facing names.
   (Migrating the whole `BOS_` env prefix across both repos is a larger follow-up.)

## 12. Container orchestration — how per-site mcpo containers are created & routed [design 2026-07-24]

**All NEW — none of this exists in the add-on today** (it has zero container management). This
EXTENDS the add-on's existing per-site provisioning (it already issues site_id, enrollment
token, site access key, and route rows, keyed in its SQLite).

### Creation — organic per-site spawn from a template
- The add-on holds ONE **mcpo compose/container template**.
- On site create/enroll it generates that site's container config:
  - name `mcpo-site-<site_id>`, joined to the **shared docker network**.
  - `--public-url https://<site_id>.ai.lighting`, `--oauth`, internal `:8351`.
  - a **volume-mounted mcpo config** whose backend = the add-on's per-site endpoint
    (`http://vendor-addon:<port>/…` over the docker net), `Authorization: Bearer <site access key>`.
- The add-on brings the container up (organic spawn); tears it down on site removal.

### Vars managed
- Per-site env injected AT SPAWN from the site's SQLite record (site_id, public URL, the site
  access key mcpo uses as its backend credential).
- Secrets stay add-on-side; injected at spawn, never baked into the image.

### Routing — front door → container
- The add-on's front-router maps `<site_id>.ai.lighting` → that site's container by **docker
  service name** (`mcpo-site-<id>:8351`). site_id is the routing key.
- Response path: mcpo (OAuth) → proxies to the add-on's per-site endpoint → client tunnel.

### The one hard constraint
- To spawn/manage containers the add-on needs **docker access — the docker socket/API
  (≈ root on the host)**. It lacks that today, and it's a real security cost against the
  lean-surface goal. Build-time sub-choice: direct socket vs a thin privileged provisioner
  the add-on calls. The routing/vars above do NOT need docker (plain HTTP over the net);
  only the spawn/lifecycle does.

## 13. Containerized LLD — per-site mcpo deployment [grounded 2026-07-24]

Full deployment: `vendor-addon` (manager, no socket) + `provisioner` (holds the socket) +
`cloudflared` (ingress) + N× `mcpo-site-<id>` (per-site plugin), all on ONE internal docker
network. Grounded in `docker-compose.gateway-poc.yml`, `Dockerfile.gateway-poc`, add-on
`src/vendor/*`.

### 13.1 Containers
**`vendor-addon`** — NEW image (no Dockerfile today), base `node:20-slim`, `node dist/vendor/index.js`.
- Ports: `443` control + front-router (internal net only; cloudflared fronts it); admin `7089` loopback INSIDE the container (never networked).
- Volumes: `vendor_addon_data:/data` (SQLite `VENDOR_DB_PATH=/data/vendor.db` + TLS); `mcpo_site_configs:/configs:rw` (writes each site's generated config).
- Env: `VENDOR_DB_PATH`, `VENDOR_ADMIN_API_KEY`, `VENDOR_DOMAIN=connect.ai.lighting`, `VENDOR_PUBLIC_DOMAIN=ai.lighting`, `VENDOR_SITE_TRANSPORT=websocket`, `VENDOR_TLS_CERT_PATH/KEY_PATH` (mounted Cloudflare origin cert), `VENDOR_SITE_LISTENER_HOST`=the bos_internal iface, + NEW `PROVISIONER_URL=http://provisioner:9090`, `PROVISIONER_API_KEY`.
- Security: `read_only`, `tmpfs /tmp`, `cap_drop [ALL]`, `no-new-privileges`. **NO docker socket.**

**`provisioner`** — NEW tiny image, the ONLY socket holder.
- Internal API `:9090`: `POST /containers`, `DELETE /containers/:id`, `GET /containers/:id/health`. Bearer `PROVISIONER_API_KEY`.
- Volume: `/var/run/docker.sock` (the one mount).
- Env: `PROVISIONER_API_KEY`, `MCPO_IMAGE=mcpo-gateway-poc:local` (pinned), `BOS_INTERNAL_NETWORK=bos_internal`.
- Hardening: reject any name not `^mcpo-site-\d{5}$`; image pinned server-side; forces the security opts/limits so the add-on can't request a privileged container.

**`mcpo-site-<id>`** (×N) — REUSE `mcpo-gateway-poc:local` UNCHANGED.
- Command = existing gateway-poc: `--config /configs/mcpo-site-<id>.json --host 0.0.0.0 --port 8351 --oauth --public-url https://<id>.ai.lighting`.
- Ports: `:8351` internal only (drop the host publish `127.0.0.1:<site>:8351`).
- Volumes: `mcpo_site_<id>_data:/data:rw` (OAuth state persistence); `mcpo_site_configs:/configs:ro`.
- Env at spawn (from SQLite): `BOS_VENDOR_SITE_ID`, `MCPO_API_KEY` (consent key), `BOS_VENDOR_SITE_ACCESS_KEY` (backend cred). Never baked in. Plus provisioner-fixed `UV_CACHE_DIR=/tmp`.
- Security **[REVISED 2026-07-25 — flexible sandbox]**: writable rootfs + internet egress (joins `bos_egress`, §13.2) so plugins can install/run tools (e.g. `uvx mcp-server-time` fetches from PyPI). KEEP HARD: `cap_drop ALL`, `no-new-privileges`, 1cpu/512m/pids256, NO docker socket, NO host ports, pinned image. The frozen `read_only`/offline posture was too tight for a plugin host; only that relaxes — the anti-privilege-escalation lockdown stays.

### 13.2 Network **[REVISED 2026-07-25 — two networks for the flexible sandbox]**
- `bos_internal` (bridge, `internal: true`) — the isolated backend hop. vendor-addon, provisioner, cloudflared, and every mcpo container join it. Docker DNS: front-router→`mcpo-site-<id>:8351`; mcpo backend→`vendor-addon:<site_id>`; add-on→`provisioner:9090`.
- `bos_egress` (bridge, NOT internal) — joined ONLY by the mcpo containers, giving plugins internet (PyPI/npm/tool APIs) without giving egress to the backend net, the add-on, or the provisioner.
- Nothing published to host except cloudflared's OUTBOUND tunnel.

### 13.3 Volumes
- `vendor_addon_data` (add-on rw) — SQLite + TLS.
- `mcpo_site_configs` (add-on rw, each mcpo ro) — the generated per-site configs. Replaces the single bind-mount `config.gateway-poc.json` (doesn't scale to N sites).
- `mcpo_site_<id>_data` (one mcpo rw) — OAuth state.
- docker.sock (provisioner only).

### 13.4 Per-site config (site 44354, `/configs/mcpo-site-44354.json`)
```json
{ "mcpServers": { "building-os": {
    "enabled": true, "type": "streamable-http",
    "url": "http://vendor-addon:44354/mcp",
    "headers": { "Authorization": "Bearer <site-44354-access-key>" } } } }
```
- `url` = the add-on's internal per-site MCP endpoint over `bos_internal` (port = site_id, keeps the §1 fail-closed guard). **Re-points the backend off the public hairpin** (§9).
- Bearer = hop-2 site access key (verified `apps.ts:182`; issued `POST /sites/:id/access-keys`).

### 13.5 Compose structure
**One static compose** for `vendor-addon` + `provisioner` + `cloudflared` + networks + volumes. The `mcpo-site-<id>` containers are created **imperatively by the provisioner** (`docker create`/`start`), NOT compose services — compose can't express an unbounded runtime-growing set, and the existing gateway-poc compose is hard-keyed to one site (`compose:1,14,16`). The provisioner reuses every gateway-poc setting, parameterized by site_id.

### 13.6 Provisioning sequence
1. `POST /enrollment-tokens` → allocates site_id, persists in SQLite.
2. `POST /sites/<id>/access-keys` → mints the backend site access key.
3. Add-on config-generator (NEW) writes `/configs/mcpo-site-<id>.json`.
4. Add-on provisioner-client (NEW) → `POST provisioner:9090/containers` → provisioner `docker create`+`start` `mcpo-site-<id>` on `bos_internal`.
5. Add-on front-router (NEW) registers `<id>.ai.lighting` → `mcpo-site-<id>:8351`. No per-site Cloudflare edit (one wildcard `*.ai.lighting`).
6. Health probe (NEW) polls `http://mcpo-site-<id>:8351/.well-known/oauth-authorization-server` until 200.
7. Live.
Teardown (`DELETE /sites/<id>`): revoke + broker close (exists) → `DELETE provisioner/containers/mcpo-site-<id>` (NEW) → drop route → delete config + data volume. Failed-provision rollback: if spawn succeeds but the health probe never greens, the orchestrator auto-removes the container + drops the route (no half-provisioned pile-up).

> **[LIVE — plugin proof, 2026-07-25]** The sandbox is config-driven and executes tools. Adding `{"time":{"command":"uvx","args":["mcp-server-time"]}}` to an mcpo config, run flexible (writable + `bos_egress` + `UV_CACHE_DIR=/tmp`), made mcpo fetch and run `mcp-server-time` and return real results: `python -m mcpo serve` → `POST /time/get_current_time {"timezone":"UTC"}` returned a live timestamp (log `Successfully connected to: mcpo, time`); `--oauth` mode mounts `/time` and gates `/mcp` with 401. mcpo `--oauth` requires the `MCPO_API_KEY` consent key or it exits. Live site 44354 untouched.

### 13.7 Ingress
`cloudflared` container on `bos_internal`. Cloudflare edge holds the `*.ai.lighting` wildcard TLS; forwards:
- `<site>.ai.lighting` → `vendor-addon:443` → front-router demuxes by Host → `mcpo-site-<id>:8351`.
- `connect.ai.lighting` → `vendor-addon:443` → tunnel routes (`/relay/connect`, `/v1/tunnels/*`, `/enroll`).
TLS: the add-on's own ACME is single-CN (`tls.ts:404`) — covers `connect.ai.lighting` only, not `*.ai.lighting`. RECOMMENDED: mount a **Cloudflare Origin CA cert** into the add-on (`VENDOR_TLS_CERT_PATH/KEY_PATH`); cloudflared→`https://vendor-addon:443`, wildcard is Cloudflare's edge cert. Zero new TLS code. (Alt: extend ACME to wildcard/SAN — real code.)

### 13.8 Security boundaries
- Host-published: nothing but cloudflared's OUTBOUND tunnel (no inbound host port).
- Add-on has NO socket → creates containers only by asking the provisioner. Compromise ≠ host root.
- Provisioner = the single privileged surface: minimal bearer-authed API, name regex, pinned image, forced security opts.
- Secrets in the add-on's SQLite, injected at spawn, never in an image. mcpo runs uid 10001, cap_drop ALL, no-new-privileges, resource-limited **[REVISED 2026-07-25: writable rootfs + `bos_egress` internet for the plugin sandbox; `read_only` dropped — see §13.1. No socket, no host ports, and pinned image still hold.]**
- Admin API stays loopback inside the add-on container.
- 4-hop auth preserved (§10).

### 13.9 Reusable vs NEW
- **REUSE:** `mcpo-gateway-poc:local` image unchanged; gateway-poc security opts/limits/command/healthcheck (into the provisioner spec); add-on SQLite/enrollment/access-keys/connectorKey/demux/per-site-listener/ACME-override/admin API.
- **NEW (all add-on side):** front-router (host demux, `@fastify/http-proxy` — already an unused dep), config-generator, provisioner-client, the provisioner container, per-container health, the docker/compose/net/cloudflared layer.
- **CHANGE (bounded):** per-site listeners bind loopback today (`site-listener-manager.ts:57-62,331`) → re-point to the `bos_internal` iface via `VENDOR_SITE_LISTENER_HOST`. site_id-as-port kept.

### 13.10 Flagged implementation risks (the "will bite you" list)
1. **Front-router MUST be a transparent proxy** — preserve `Host` + path so mcpo's OAuth issuer/discovery (`https://<site>.ai.lighting`) matches what ChatGPT sees. Rewrite the Host and OAuth discovery/redirects break. (Caught in review; not in the draft.)
2. **Ingress TLS** — Cloudflare origin cert (recommended) vs ACME wildcard (real code). Build choice.
3. **Provisioner API shape + language** — proposed, not yet settled.
4. **Per-site backend port model** — site_id-as-port (recommended, keeps fail-closed) vs single path-based listener.
