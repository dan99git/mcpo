# Scratch: Vendor add-on MCPO container stack

> Status: SCRATCH, NON-CANONICAL, BUILD AND WARGAME RECORD
>
> Date: 2026-07-23
>
> This document records the current target stack. It does not replace anything under `architecture-docs/`.

## 1. Fixed decisions

- Every customer site receives a five-digit starter `site_id` for internal and offline operation before a MikroTik identity is available.
- A site without a MikroTik may retain its starter `site_id` permanently, but it cannot activate public or external tooling.
- Public or external tooling requires a router-minted `site_id` derived from the MikroTik server's canonical unique MAC ID.
- The MAC ID is normalized and hashed into a collision-checked five-digit `site_id`.
- Stored identity provenance is explicit: `starter` or `router_minted`. It is never inferred from the number.
- The gateway allocates starter IDs from the operating-system cryptographic random source, collision-checks them locally and persists them before use.
- Replacing a starter ID with a router-minted ID is a one-time, journalled state transition. It is not a single SQLite transaction because the current identity is also stored in files, devices, RouterOS state and a running listener.
- A valid `site_id` is in the TCP port range `10000..65535`.
- For a router-minted ID, the vendor registry collision-checks every candidate before allocation.
- A router-minted collision is handled by the same deterministic rehash sequence and the accepted attempt is stored.
- The active `site_id` is also the actual HTTP listening port of the BOS FastMCP service on the customer gateway.
- In steady state there is no second local FastMCP port. A numeric re-key stops the old listener before starting the new listener; a bounded one-time interruption is accepted.
- For public tooling, the tunnel client forwards directly to `http://127.0.0.1:<site_id>/mcp`.
- Each public-tooling site receives one isolated MCPO container on the vendor host.
- When public tooling is active, the public MCP endpoint is `https://<site_id>.ai.lighting/mcp`.
- One shared public HTTPS edge terminates TLS and routes only by hostname and path. It owns no OAuth, identity, grants, browser sessions, customer tenancy or tunnel queue.
- The shared edge routes `/mcp`, `/ui` and `/session` for one hostname to that site's isolated container.
- Inside each site container, the MCPO tool endpoint and vendor page/session application remain separate component boundaries even when they share the same deployable unit for the prototype.
- Each site MCPO aggregates one container-local `vendor-site` MCP provider and one site-bound tunneled `building-os` MCP backend.
- Vendor-local tools, page sessions, swipe cards, ReCAD artifact work and proposal finalisation never enter the gateway tooling tunnel. Only the allowlisted gateway read, import and verification MCP calls cross it.
- OAuth is a separate vendor service. It owns users, clients, site grants, token issuance, refresh, revocation and end-user OAuth state.
- Each site MCPO validates externally issued, site-scoped tokens. It does not mint tokens or persist end-user OAuth state.
- The vendor tooling tunnel transports gateway MCP tool requests and responses only. It owns no OAuth, customer tenancy, pages, browser sessions, HTML, assets, cookies or model service.
- Public ChatGPT authorization is completed once against the separate OAuth service. Edge, container and tunnel reconnects do not repeat authorization while that external grant and its tokens remain valid.
- Optional inference is separate from tool access. MCPO forwards inference to configured model providers. It does not supply model compute itself.

## 2. Full public request chain

In this section, `<site_id>` means the active router-minted public site ID. A starter-only site does not enter this chain.

```text
ChatGPT Desktop
  -> HTTPS with a token issued by the separate OAuth service
shared public HTTPS edge :443
  -> TLS termination and hostname/path routing only
https://<site_id>.ai.lighting/mcp
  -> site container /mcp
site MCPO component
  -> validate the external token and its site scope
  -> site-bound internal credential
BOS vendor tunnel ingress
  -> durable request queue
OpenAI tunnel client poll and response protocol
  -> outbound connection held by the customer gateway
gateway 127.0.0.1:<site_id>/mcp
  -> BOS FastMCP tool execution
BOS gateway 127.0.0.1:7085
  -> commissioned Building OS devices and services
```

The protected page path is separate from the tooling tunnel:

```text
browser or ChatGPT page surface
  -> HTTPS
shared public HTTPS edge :443
  -> TLS termination and hostname/path routing only
https://<site_id>.ai.lighting/ui or /session
  -> site container page/session component
  -> separate OAuth service for authorization
  -> vendor-side page, session and artifact data
```

`/ui`, `/session`, HTML, assets, cookies and browser-session traffic never enter the gateway tooling tunnel.

The aggregated MCP path has two bounded providers:

```text
site MCPO aggregator
  |-- vendor-site provider
  |     -> bound-site checks, swipe-card/session work, ReCAD artifacts and population orchestration
  |     -> vendor-local execution stays inside the site container
  |
  `-- building-os backend
        -> fixed allowlisted gateway read, import and verification MCP calls
        -> tooling tunnel -> assigned gateway only
```

The model cannot select a tunnel, gateway, site binding or unlisted gateway tool.

After the controlled public re-key, the active five-digit number has one meaning throughout the public and private path:

```text
site_id
  = vendor site record
  = public hostname label
  = vendor host port
  = customer gateway FastMCP port
```

Before public re-key, the starter ID identifies the internal site and FastMCP port only. After a numeric re-key, the old starter ID is retired locally and retained only in the transition audit and archived package lineage. It is not an alias, redirect or second listener.

The opaque `tunnel_id` is separate. It is not exposed as the customer address and is not derived from the MAC ID.

## 3. Existing foundations

### 3.1 Building OS gateway

The existing code already provides:

- a five-digit site ID validator covering `10000..65535`;
- SHA-256 candidate generation with deterministic retry attempts;
- a site listener bound to `127.0.0.1:<site_id>`;
- a bounded FastMCP projection of the canonical BOS tool catalogue;
- bearer authentication for the local MCP service;
- BOS tool execution through the gateway on port `7085`;
- outbound tunnel enrollment, credentials, reconnect and non-replay behavior.

Required changes:

- implement the frozen cryptographic-random five-digit starter-ID allocator, local collision checks and pending-to-active gate;
- persist the frozen identity provenance and transition journal;
- implement the frozen starter-to-router-minted re-key state machine and recovery gates;
- replace the current identity input with the canonical normalized MikroTik MAC ID for router-minted allocation;
- make the BOS FastMCP process accept and validate the current active `site_id` as its listening port;
- start FastMCP on the starter ID before public enrollment and move it through the journalled re-key process when the active ID changes;
- point the tunnel client directly at the site-ID FastMCP URL;
- retire the extra local forwarding hop from the target runtime;
- update installers, health checks, tests and service supervision to use the site-ID port;
- keep port `7085` as the private BOS gateway API behind FastMCP.

### 3.2 MCPO

The existing MCPO code already provides:

- Streamable HTTP MCP aggregation at `/mcp`;
- self-hosted OAuth discovery, dynamic client registration, authorization, access tokens and refresh tokens;
- persistent OAuth client and token state;
- remote Streamable HTTP MCP backends with configured headers;
- provider registration and OpenAI-compatible completion forwarding;
- a working Uvicorn process and public OAuth discovery through the existing Cloudflare route.

The self-hosted OAuth mode is an existing experiment, not the target authorization boundary for this stack.

Required changes:

- a hardened site-container image;
- external OAuth issuer and site-scope validation at `/mcp` without local token minting or end-user OAuth persistence;
- explicit read-only MCPO configuration and writable page/session state paths;
- site-specific readiness and liveness checks;
- failure of readiness when the configured external authorization validation contract cannot be applied;
- site-specific internal tunnel configuration;
- rejection of tokens issued for another site;
- removal or disabling of per-container OAuth registration, authorization, consent and token-issuance routes;
- a separate page/session component in the same prototype container for `/ui` and `/session`;
- container lifecycle support from the vendor supervisor;
- separate authorization and accounting if optional inference is enabled.

### 3.3 OpenAI tunnel client

The tunnel client is the gateway-side agent and published protocol. It supplies:

- custom control-plane base URL support;
- tunnel metadata lookup;
- long polling for commands;
- correlated response posting;
- request deadlines and reconnect behavior;
- bounded request processing;
- MCP transport handling.

It does not supply the production BOS vendor service. BOS must implement the control plane, durable queue, site binding, public ingress and operations layer.

## 4. Site identity contract

### 4.1 Identity lifecycle

Every site starts with a five-digit `site_id` in `10000..65535`. Its provenance is stored separately:

```text
starter
  -> internal and offline operation
  -> may remain permanent when no MikroTik exists

router-minted
  -> derived from the canonical MikroTik MAC
  -> required before public or external tooling is activated
```

The starter ID is not presented as MikroTik-derived. A site must not expose a public hostname, MCPO container or vendor tunnel while its active identity is still the starter ID.

The gateway site registry is the authority for starter allocation. The vendor is the authority for router-minted public allocation.

The gateway stores these identity fields on the site record:

```text
site_id
starter_site_id
starter_allocation_method: starter-random-v1
starter_allocation_draw: positive integer
identity_source: starter | router_minted
identity_state: pending | active | recovery_required
hardware_mac_hash: null for starter, required for router_minted
derivation_attempt: null for starter, required for router_minted
```

The gateway also stores `site_identity_allocations(candidate_site_id PRIMARY KEY, method, draw_number, state, created_at, retired_at, last_error)`. Allocation state is `pending`, `assigned` or `retired`. Every candidate successfully inserted for this gateway remains in this ledger permanently, including a candidate abandoned after a listener race.

Every attempted public transition is also stored in `site_identity_transitions` with a unique transition ID, old and new site IDs, hardware MAC hash, derivation attempt, state, timestamps and last error code. The transition record is the recovery journal and the retired-ID record.

### 4.2 Starter allocation

Starter allocation has no hardware input and does not pretend to be a hash:

1. Draw a uniform integer from `10000..65535` with the operating-system cryptographic random source.
2. Reject it if it exists in `sites` in any state, exists in `site_identity_allocations` in any state, or is an old or new ID in an unfinished or completed local transition.
3. Reject it if `127.0.0.1:<candidate>` is already occupied.
4. Retry up to 1,000 times.
5. Inside one gateway `BEGIN IMMEDIATE` transaction, insert the allocation as `pending` and insert the site as `starter` and `pending`. Store allocation method `starter-random-v1` and the draw number on both records. A unique-key conflict counts as a rejected draw and continues within the 1,000-draw limit.
6. Start FastMCP on the candidate and run authenticated health and catalogue checks.
7. Change the site to `active` and the allocation to `assigned` in one transaction only after those checks pass.

Starter uniqueness is local to one gateway database. A starter ID is not globally reserved and must never be used as proof of a public identity.

If the listener loses the port race after allocation, setup exposes no site operation. In one `BEGIN IMMEDIATE` transaction the gateway marks that allocation `retired` and removes its empty pending site row. A retry may allocate another candidate only while no ReCAD package, hierarchy, placement, device imprint or other site data exists. Once the starter reaches `active`, it is stable until the one permitted router-minted transition.

### 4.3 Canonical MikroTik hardware input

The identity adapter must return one stable normalized MAC value for the MikroTik server. The exact RouterOS property must be named in the implementation contract and used everywhere. An arbitrary interface MAC cannot be selected at runtime.

Normalization must produce one byte representation regardless of input punctuation or letter case. For example, all accepted textual representations of the same MAC must produce the same normalized value.

### 4.4 Router-minted five-digit allocation

The existing candidate pattern can be retained for the router-minted ID with the MAC as its input:

```text
attempt 0 input: <normalized_mac>
attempt N input: <normalized_mac>#<N>
digest: SHA-256(input)
candidate: deterministic digest reduction
accept only: 10000..65535
```

Router-minted allocation begins inside one vendor database transaction:

1. Derive the next candidate.
2. Reject a candidate outside `10000..65535`.
3. Reject a candidate already assigned to another hardware record.
4. Retry deterministically.
5. Store the accepted `site_id`, hardware hash and rehash attempt together in a private reservation.

The raw MAC is an identity input, not an authentication secret. The connector key proves possession of an enrolled gateway.

The reservation does not create a vendor site listener, tunnel credential, MCPO container, OAuth client, access key, hostname or Cloudflare route. It is idempotently bound to the enrollment request and remains private until the gateway confirms the local transition.

The gateway independently validates the returned candidate from the normalized MAC and recorded attempt. A candidate equal to this site's current starter ID is permitted only when that port is owned by this site's current FastMCP listener. A different candidate is rejected if it is occupied by another local site, a retired local identity or an unrelated process already bound to `127.0.0.1:<candidate>`. The vendor records that rejection and replaces the candidate in a new immediate transaction under the same reservation and idempotency key.

### 4.5 Chain of custody

The vendor record must link:

```text
starter_site_id
  -> internal and offline operation
  -> optional retained identity for a no-MikroTik site

hardware_mac_hash
  -> rehash_attempt
  -> router_minted_site_id
  -> identity_transition_id
  -> tunnel_id
  -> MCPO container
  -> public hostname
  -> customer and site
```

The gateway always stores the active site ID and its provenance. After a numeric transition, the old starter ID remains in `site_identity_transitions` and in a read-only archived site package. It is never allocated again on that gateway and does not remain live as an alias, listener, route or hostname.

In public-tooling state the gateway also stores the tunnel ID and connector credential, and rechecks the local MikroTik hardware binding before opening the tunnel.

### 4.6 One-time transition state machine

Only a site whose source is `starter` may enter this state machine. A `router_minted` site cannot be re-keyed through it. Router replacement or identity transfer is a separate contract.

```text
starter_active
  -> public_id_reserved
  -> migration_prepared
  -> cutover_in_progress
  -> router_minted_local
  -> tunnel_authenticated
  -> site_app_ready
  -> route_verified
  -> public_active
```

The permitted behavior at each boundary is:

1. `public_id_reserved`: the vendor holds only the private MAC-derived reservation. The starter site remains active.
2. `migration_prepared`: for a numeric change, the gateway has validated every transformed key, staged the new package, prepared RouterOS changes and prepared every commissioned device identity. An equal-ID transition records a validated no-op migration. The starter remains active.
3. `cutover_in_progress`: site writes are locked and the starter FastMCP listener is stopped. No public component exists.
4. `router_minted_local`: the gateway database, site package, devices, RouterOS state and new FastMCP listener all verify under the router-minted ID. The old listener is closed.
5. `tunnel_authenticated`: the tunnel credential exists and the gateway has proved an authenticated poll or session.
6. `site_app_ready`: the isolated container is healthy, MCPO validates only externally issued tokens for this site, its page/session component is healthy, and MCPO discovers only this site's tunnel.
7. `route_verified`: the shared edge routes this hostname's `/mcp`, `/ui` and `/session` paths to the correct private container, and one safe MCP read reaches the correct gateway.
8. `public_active`: the separate OAuth service may activate customer grants for this site.

If the router-minted candidate equals the starter ID, the numeric migration, spatial rewrite and port move are skipped. The state moves from `migration_prepared` directly to `router_minted_local` after the router binding, provenance change and local health proof commit. The gateway still records the transition, MAC hash and derivation attempt, and every public activation gate still applies.

A failure before `cutover_in_progress` cancels the prepared local work, releases the vendor reservation and returns to `starter_active`. A failure after cutover begins sets `recovery_required`, keeps every public component disabled and resumes from the journal. It does not silently allocate another ID or automatically oscillate between IDs. A failure after `router_minted_local` leaves the new local identity active and retries only the failed public stages.

No steady-state dual listener is allowed. A numeric transition stops the starter listener, commits the local move, starts the router-minted listener and accepts the resulting one-time interruption.

### 4.7 Re-key migration surface

The gateway database move is one `BEGIN IMMEDIATE` transaction with deferred foreign-key checks. It must preflight every transformed primary and unique key, update the complete typed surface, require `PRAGMA foreign_key_check` to return no rows, and then commit. The current schema uses `ON UPDATE NO ACTION`, so a direct primary-key update is invalid.

The typed database surface is:

- `sites.site_id` and its provenance fields;
- `sites.subnet_cidr`, recomputed with the canonical site-subnet derivation for the new site ID so the stored compatibility column cannot retain the starter subnet;
- `buildings.site_id`, `border_router_infrastructure.site_id` and `routers.site_id`;
- ReCAD-owned building, floor and space IDs and space slugs containing the old site ID;
- every child reference to those hierarchy IDs, including `devices.space_id`, `device_placement.space_id`, `planned_positions.space_id` and border-router infrastructure references;
- `planned_positions.spatial_id` and `device_placement.spatial_id`;
- typed ReCAD and border-router identity fields in metadata and `devices.properties.infrastructure`.

Opaque metadata, automation payloads and CBOR are not search-and-replaced. Anything that may contain an old site or spatial selector is invalidated and rebuilt from the migrated typed model.

The filesystem move is staged beside the active package and validated before cutover. It covers:

- `data/Workspace/site-manager/<site_id>/`;
- the per-site `spatial-model.json`, `tags.json` and `imports/last-import.json`;
- every embedded spatial ID and stored absolute package path;
- `_active-site.json` and the shared `data/Workspace/spatial-model.json` when this is the active site;
- filesystem device-registry records containing site, spatial or `recad-<site_id>-...` references.

The old package is moved to `data/Workspace/site-manager/.archive/<transition_id>/<old_site_id>/` after the new package and active pointer verify. The archive is read-only and excluded from active package discovery. The active-site and device-registry caches are then cleared and rebuilt.

Commissioned device identity is part of the transaction boundary even though it cannot be covered by SQLite. The current immediate `/api/commission` write is insufficient. Mature-site re-key requires an idempotent device protocol that:

1. prepares the new spatial ID beside the old active ID under the transition ID;
2. reports the prepared value without activating it;
3. activates only after the gateway enters cutover;
4. reports the active transition after reboot; and
5. can complete an explicit journal-driven rollback before local commit.

Until that protocol exists, any site with a commissioned spatial identity fails re-key preflight. It must not be migrated device by device on a best-effort basis.

The RouterOS and derived-artifact surface also moves before `router_minted_local`:

- the control subnet derived from `site_id`;
- local and peer WireGuard or site-link interface addresses, routes and allowed-addresses derived from that subnet;
- operational phonebooks;
- compiled automations and ledgers containing site or spatial selectors.

Every required peer, imprinted device and local service must be reachable and prepared before cutover. If any required participant cannot prepare, the starter remains active and public provisioning stops.

Reachability alone is not enough for peer RouterOS state. Existing site links require an idempotent transition operation that stages the new interface addresses, routes and allowed-addresses under the transition ID, activates them during cutover, verifies every peer and retains an explicit rollback plan. Until that exists, a site with derived peer routes fails re-key preflight.

## 5. Deployable units

### 5.1 Gateway runtime

Every gateway installation contains:

- a persisted active site ID and identity provenance;
- BOS FastMCP bound to `127.0.0.1:<active_site_id>`;
- BOS gateway bound to `127.0.0.1:7085` or the existing private gateway bind;
- local service supervision.

A public-tooling gateway additionally contains:

- MikroTik identity reader;
- enrollment client;
- persisted site and tunnel credentials;
- OpenAI tunnel client;
- reconnect handling.

A local-only installation without a MikroTik retains its starter site ID and omits vendor enrollment, tunnel credentials, MCPO provisioning and public hostname activation.

After public re-key, the tunnel client is configured with:

```text
control-plane base URL: BOS vendor service
tunnel ID: opaque vendor-issued identifier
tunnel credential: gateway-specific secret
local MCP URL: http://127.0.0.1:<site_id>/mcp
local MCP bearer: gateway-local FastMCP key
```

### 5.2 Vendor tunnel control plane

This service is transport for gateway MCP tool requests and responses only. Its site binding exists only to route one internal MCPO ingress credential to one gateway tunnel. It does not own customer tenancy, end-user identity, OAuth, grants, pages, browser sessions, HTML, assets, cookies or model service.

The first single-host implementation contains:

- enrollment API;
- tunnel metadata API;
- poll API;
- response API;
- internal MCP ingress for site MCPO containers;
- durable request queue;
- request leases and deadlines;
- credential verification and revocation;
- site and tunnel state reconciliation;
- bounded audit records;
- health and readiness reporting.

The OpenAI client contract requires these external gateway endpoints:

```text
GET  /v1/tunnels/{tunnel_id}
GET  /v1/tunnels/{tunnel_id}/poll
POST /v1/tunnels/{tunnel_id}/response
```

The BOS internal MCPO ingress path must be defined and tested by the vendor service. It is not provided by the OpenAI client repository.

### 5.3 Per-site application container

Each site container receives:

- generated MCPO configuration;
- the site's public base URL;
- one external OAuth validation configuration bound to this site;
- one internal tunnel-ingress credential;
- one private page/session state boundary;
- optional provider credentials only when inference is enabled.

The prototype container exposes three routed surfaces behind one private container origin:

```text
/mcp      -> MCPO tool component
/ui       -> vendor page component
/session  -> vendor browser-session component
```

These components may share the same isolated per-site container for the prototype, but their code, state and authorization responsibilities remain separate. MCPO owns MCP aggregation and protocol handling. The container-local `vendor-site` MCP provider owns the vendor-side workflows. The page/session component owns protected page delivery, browser-session exchange and page artifacts. Neither component owns users, OAuth clients, grants, access-token issuance, refresh-token issuance or end-user OAuth persistence.

The separate OAuth service issues site-scoped tokens. MCPO validates those tokens against the configured authority and rejects a token for any other site. The page/session component uses the same authority to authorize its own surface, then manages only its browser-session and artifact state. The token-validation details, AI-host component-metadata API and post-exchange cookie contract remain to be frozen.

The container listens on `0.0.0.0:8351` only inside its network namespace. Docker publishes it only to the vendor host loopback address:

```text
127.0.0.1:<site_id>:8351
```

The generated MCPO configuration aggregates exactly two site-bound providers. This is a target configuration shape, not a claim that the current MCPO config supports every required filter:

```json
{
  "mcpServers": {
    "vendor-site": {
      "type": "streamable-http",
      "url": "<container-local vendor-site MCP URL>"
    },
    "building-os": {
      "type": "streamable-http",
      "url": "<BOS vendor allowlisted MCP ingress for this tunnel>",
      "headers": {
        "Authorization": "Bearer ${SITE_INTERNAL_INGRESS_KEY}"
      }
    }
  }
}
```

The exact local-provider and internal-ingress URLs are deployment contracts to be built. They must not be supplied by caller input. The `building-os` projection exposes only the gateway calls required for bound-site reads, remote-safe ReCAD import and post-import verification. The raw gateway catalogue is not published through this public stack.

The `vendor-site` provider exposes these explicitly proposed tool IDs. None is claimed to exist in the current MCPO or BOS catalogues:

| Proposed tool ID | Execution boundary | Gateway use |
| --- | --- | --- |
| `bos_bound_site_context` | Vendor provider validates the external grant, container binding and enrolled site | Allowlisted site reads only |
| `bos_get_swipe_card` | Vendor session component creates one bounded, one-use card | None |
| `bos_recad_upload` | Vendor artifact component resolves the host file reference, hashes and stores it against subject and site | None |
| `bos_recad_inspect` | Vendor artifact component parses the stored file and reports discovered data, gaps and warnings | None |
| `bos_recad_finalise_proposal` | Vendor artifact component applies resolved gaps and freezes the import proposal and proposal hash | None |
| `bos_site_populate_from_recad` | Vendor provider checks confirmation, orchestrates import and verifies gateway truth | Allowlisted import and verification calls only |

`bos_recad_finalise_proposal` is read-only with respect to the gateway. It must reject stale proposal hashes and mismatched subject, grant, site or artifact bindings. `bos_site_populate_from_recad` accepts only a finalised proposal hash and explicit confirmation. It cannot accept a caller-selected site ID, gateway URL, tunnel ID, server path or raw gateway tool name.

The target `bos_get_swipe_card` handoff is not model-visible:

1. Ordinary tool content contains only the bound site, allowed surface, expiry and handoff readiness. It contains no raw swipe card, session cookie or card-bearing URL.
2. The one-use card and fixed exchange endpoint are returned only in host/component metadata that is not included in model-visible tool content.
3. The trusted component sends the card in the body of a `POST` to the fixed `/session/exchange` endpoint.
4. The session component atomically consumes the card, sets the protected browser-session cookie and returns `303 See Other` to a clean allowlisted `/ui/...` URL.
5. The card is absent from the request URL, redirect URL, ordinary tool result, transcript and page markup.
6. There is no query-token, fragment-token or model-visible URL fallback. If the host cannot deliver component-only metadata and perform the POST, the handoff fails closed.

This handoff is not accepted on document reasoning alone. A live ChatGPT/browser-host test must prove metadata remains non-model-visible, the POST and cookie occur in the page's browser context, replay fails, the redirect URL is clean, and revocation stops the resulting session.

### 5.4 Site supervisor

The supervisor owns desired and actual site deployment state. It must:

- create private router-minted ID reservations transactionally without creating public assets;
- advance only the frozen, journalled starter-to-router-minted transition states;
- issue and rotate credentials;
- create site configuration and state volumes;
- start and replace containers;
- bind only the allocated host port;
- reuse the current Cloudflare route method for the site hostname;
- verify route-to-origin alignment;
- reconcile missing, stopped or unhealthy containers;
- revoke or suspend one site without affecting others;
- preserve state across image replacement;
- record the image digest actually running.

The current vendor implementation does not meet this order. Issuing an enrollment token creates the vendor site row and binds its listener, `/cloudflare-routes` lists that site before gateway enrollment, and the gateway commits its credential file before local site adoption. The build must replace that sequence with private reservation, local transition proof, tunnel proof, MCPO readiness and route-last activation.

## 6. Vendor data model

SQLite is sufficient for the first single-host implementation if allocation and request leasing are transactional.

| Record | Required information |
| --- | --- |
| `site_identity_reservations` | reservation ID, customer and gateway binding, starter lineage, hardware MAC hash, derivation attempt, reserved router-minted ID, idempotency key, state and timestamps |
| `sites` | active router-minted `site_id`, starter lineage, hardware MAC hash, derivation attempt, transition ID, customer binding and public-tooling state |
| `tunnels` | opaque tunnel ID, `site_id`, connector-key hash, version, last poll, state |
| `site_app_instances` | `site_id`, container identity, image digest, host port, public URL, provider configuration, volume, desired and actual state |
| `cloudflare_routes` | `site_id`, hostname, local origin, route identity, state |
| `requests` | request ID, tunnel ID, shard token, deadline, payload, state, lease, result timestamps |
| `authorization_bindings` | `site_id`, external OAuth authority identifier, resource or audience binding, policy version and readiness state; no end-user tokens |
| `swipe_cards` | `site_id`, card hash, subject and grant binding, allowed surface, expiry, consumed or revoked state and timestamps |
| `page_sessions` | `site_id`, opaque session reference or hash, authorization binding, expiry, revocation and timestamps |
| `recad_artifacts` | artifact ID, `site_id`, subject binding, filename, hash, size, media type, state, expiry and timestamps |
| `recad_proposals` | artifact ID, inspection hash, finalised proposal hash, resolved gaps, state and timestamps |
| `audit_events` | actor, site, action, target, result, timestamp |

The separate OAuth service owns users, clients, grants, access tokens, refresh tokens and their revocation records. The gateway database separately adds the site provenance fields, `site_identity_allocations` ledger and `site_identity_transitions` journal defined in Section 4. Reservation records are private and cannot be selected by the public router. `sites`, tunnel, MCPO, route and authorization-binding records are created or activated only after the gateway proves `router_minted_local`.

Secrets are not stored in audit events or ordinary API responses. Vendor connector and internal verification-only bearers are stored as hashes. The site container stores no end-user OAuth access or refresh tokens. Browser-session state is private, permission-restricted, excluded from images and logs, and excluded from backups unless those backups are encrypted. Secrets that MCPO, the page/session component or the tunnel agent must use are stored through the host secret boundary and mounted only into their assigned process.

## 7. Credential boundaries

A public-tooling site has seven separate credential boundaries:

1. Externally issued OAuth access or refresh token, scoped to one site resource. The separate OAuth service owns issuance, persistence and revocation.
2. One-use swipe card, bound to one subject, grant, site and allowlisted surface. Only the trusted component receives it and submits it in a POST body.
3. Site browser-session credential, valid only for that site's `/ui` surface and owned by the page/session component.
4. Site MCPO internal ingress credential, valid only for one tooling tunnel.
5. Gateway tunnel credential, valid only for metadata, poll and response operations on one tunnel.
6. Gateway-local FastMCP bearer, valid only on `127.0.0.1:<active_site_id>/mcp`.
7. BOS gateway API key, valid only between FastMCP and the private gateway API.

No credential crosses into the next boundary.

Public OAuth headers terminate at the site MCPO component for `/mcp` and at the page/session component for its authorization exchange. They never enter the tooling tunnel. The tunnel service consumes the site internal credential and must not place it in the command sent to the gateway. The tunnel client then applies the gateway-local FastMCP bearer to the direct site-ID-port request.

## 8. Provisioning workflow

1. Install the BOS gateway runtime.
2. Allocate the five-digit starter ID with `starter-random-v1`.
3. Persist the site as `starter` and `pending`.
4. Start BOS FastMCP on `127.0.0.1:<starter_site_id>`.
5. Verify authenticated FastMCP health and catalogue identity, then mark the starter `active`.
6. If public or external tooling is not required, provisioning ends here and the starter remains active.
7. When public tooling is requested, require one active starter site, no unfinished identity transition and no existing public assets.
8. Read and normalize the canonical MikroTik MAC and verify the hardware binding locally.
9. Generate the gateway connector secret and enrollment idempotency key locally.
10. Submit the one-time enrollment token, hardware identity proof, connector-key hash material and current starter lineage to the vendor.
11. Reserve a collision-free router-minted ID in the private vendor reservation table. Create no listener, tunnel, container, access key, hostname or route.
12. Independently validate the candidate and derivation attempt on the gateway. Reject another local site's active or retired ID. If the number differs from the starter, reject an unrelated process occupying the gateway port. If the number is equal, prove the existing listener belongs to this site. Make the vendor advance the deterministic attempt after a rejection.
13. If the numeric ID changes, preflight the complete database, package, device, RouterOS and derived-artifact migration surface. If it is equal, preflight only the provenance, router binding and public gates.
14. For a numeric change, stage the new package and prepare device and RouterOS changes. For an equal ID, record the validated no-op. Persist `migration_prepared` in either case.
15. Enter site maintenance, lock site writes and stop the starter FastMCP listener when the numeric ID changes.
16. For a numeric change, run the deferred-foreign-key database transaction, promote the staged filesystem package and activate the prepared device and RouterOS changes. For an equal ID, commit only the router binding, provenance and transition record. An interrupted cutover enters `recovery_required` and creates no public assets.
17. Verify FastMCP on `127.0.0.1:<router_minted_site_id>`. For a numeric change, start it on the new port and prove the old port is closed. Record `router_minted_local` only after the applicable checks pass.
18. Send the idempotent local-transition proof to the vendor.
19. Activate the vendor site and opaque tunnel identity, issue the tunnel credential once and bind both to the router-minted ID.
20. Persist the enrollment record through the protected gateway credential store and recheck the local MAC binding.
21. Start the tunnel client and verify its first authenticated poll or session.
22. Register the site resource and its grant policy with the separate OAuth service. Do not create end-user OAuth state inside MCPO.
23. Create the per-site application configuration, local `vendor-site` provider, allowlisted tunneled `building-os` provider, external OAuth validation binding, page/session and artifact state boundaries, and internal tooling-tunnel credential.
24. Start the isolated site container and bind host loopback port `<router_minted_site_id>` to its private application port.
25. Verify private container health, wrong-site token rejection, exact proposed vendor-tool projection, allowlisted gateway-tool projection, page/session and artifact readiness, assigned-tunnel isolation and the assigned BOS catalogue.
26. Configure the shared HTTPS edge last and verify the site hostname routes `/mcp`, `/ui` and `/session` to the correct private container without owning authorization logic.
27. Complete ChatGPT OAuth once against the separate OAuth service and call `bos_bound_site_context` through the local vendor provider.
28. Call `bos_get_swipe_card`, deliver its one-use card through non-model-visible component metadata, POST it to `/session/exchange`, receive the session cookie and follow the clean `303` redirect to one protected page.
29. Upload, inspect and finalise one ReCAD artifact entirely vendor-side, then run one explicitly confirmed `bos_site_populate_from_recad` orchestration through the allowlisted gateway import and verification calls.
30. Prove that vendor-local ReCAD work, `/ui`, `/session`, HTML, assets, cards and cookies did not enter the tooling tunnel, then mark the site `public_active`.

The internal site is active after steps 1 through 5 pass. Public tooling is active only after step 30 passes. A reservation, database row, bound listener, tunnel session, container, route or OAuth registration is not sufficient by itself.

## 9. Public tool request workflow

MCPO dispatches proposed `bos_bound_site_context`, `bos_get_swipe_card`, `bos_recad_upload`, `bos_recad_inspect`, `bos_recad_finalise_proposal` and `bos_site_populate_from_recad` to the container-local `vendor-site` provider. Local steps remain inside the container. `bos_bound_site_context` and `bos_site_populate_from_recad` may invoke only their fixed gateway read, import and verification operations through the `building-os` backend. They cannot forward a caller-supplied tool name or arbitrary MCP request.

The model-visible gateway projection is limited to approved read tools. Gateway import and verification operations required by `bos_site_populate_from_recad` are available only to that confirmed vendor-side orchestration path. The exact allowlist must be frozen before implementation.

For an allowlisted gateway operation, the request chain is:

1. ChatGPT calls `https://<site_id>.ai.lighting/mcp` with a site-scoped token issued by the separate OAuth service.
2. The shared edge terminates TLS and routes the hostname and `/mcp` path to the isolated site container. It performs no OAuth or grant operation.
3. The site's MCPO component validates the external token and rejects a token not scoped to this site.
4. MCPO or the vendor provider selects the fixed `building-os` backend for an allowlisted gateway operation. Request data cannot choose another site, tunnel or gateway tool.
5. MCPO calls the vendor internal ingress with the site-specific internal credential.
6. The tunnel service resolves the credential to exactly one tunnel.
7. The tunnel service assigns a request ID, deadline and durable queue state.
8. The gateway tunnel client receives the MCP command through long polling.
9. Public and vendor authorization headers are absent from the forwarded command.
10. The tunnel client applies the gateway-local FastMCP bearer.
11. The request is sent directly to `http://127.0.0.1:<site_id>/mcp`.
12. FastMCP validates the request against the canonical BOS catalogue.
13. FastMCP executes the tool through the private BOS gateway on port `7085`.
14. The result returns through the same request ID and tunnel.
15. The tunnel service closes the request record and returns the MCP response to the originating MCPO component.
16. MCPO returns the response to ChatGPT.

Authentication does not bypass BOS write confirmation. A write tool still requires its declared confirmation input and post-write read-back rules.

Vendor-local tool steps and protected page requests follow the container-local paths in Sections 2 and 5.3. They do not use this gateway workflow or the tooling tunnel.

## 10. Public-tooling reconnect and restart workflow

### 10.1 ChatGPT transport reconnect

- ChatGPT reconnects to the same public MCP URL.
- ChatGPT presents a token issued by the separate OAuth service.
- MCPO validates it without loading local end-user OAuth state.
- No customer OAuth action is required while the external grant remains valid.

### 10.2 Site container restart or replacement

- Restart the site container with the same site configuration, authorization binding and page/session state boundary.
- External OAuth clients, grants, access tokens and refresh tokens are unaffected because MCPO does not own or persist them.
- Browser-session survival is owned by the page/session component and must meet its separately frozen persistence contract.
- The public hostname and host port remain unchanged.

### 10.3 Gateway tunnel loss

- The tunnel client reconnects and resumes authenticated polling.
- The BOS FastMCP process remains bound to the site-ID port.
- No customer OAuth action is required.
- A new call after reconnect may proceed.

### 10.4 Interrupted requests

- A queued request not yet delivered may be recovered from the durable vendor queue if its deadline remains valid.
- A request known not to have reached FastMCP may fail or be redelivered according to the protocol contract.
- A request delivered to FastMCP but missing a final response is `unknown`.
- An `unknown` request is never replayed automatically, especially when it may have written site state.

### 10.5 Vendor restart

- Site and tunnel records reload from SQLite.
- Queue leases are reconciled.
- Undispatched requests remain bounded by their original deadlines.
- Dispatched requests without a proven result become `unknown`.
- Gateways resume polling with their existing tunnel credentials.

## 11. Revocation workflow

| Revoked item | Required result |
| --- | --- |
| External OAuth grant | The separate OAuth service revokes the grant; site MCPO and page/session access stop within the frozen validation-cache bound |
| Browser session | The page/session component can no longer serve protected pages for that session |
| MCPO internal credential | The site container cannot enter the vendor tunnel |
| Tunnel credential | The gateway cannot poll or post responses |
| Site | Public tool access, container readiness and tunnel delivery stop |
| Public MAC binding | Gateway reconnect stops until an explicit new enrollment |
| BOS local key | Tunnel-delivered requests fail at the local FastMCP boundary |

Site-wide grant and token revocation belongs to the separate OAuth service, not MCPO or the tooling tunnel. Browser-session revocation belongs to the page/session component. Revocation must be site-specific and must not restart or invalidate another customer container.

## 12. Container security contract

Each site container must run with:

- a non-root user;
- read-only root filesystem;
- read-only generated configuration;
- one private writable page/session and runtime-state boundary;
- private temporary storage;
- dropped Linux capabilities;
- no privileged mode;
- no Docker socket;
- no host or customer filesystem mounts;
- no OAuth token-minting keys or end-user OAuth state;
- no shared provider credential file;
- bounded CPU, memory and process count;
- a site-specific network policy;
- only its assigned host loopback port published.

Containers reduce accidental and application-level tenant crossover. They still share the vendor host kernel, so the host, Docker daemon and supervisor remain privileged security boundaries.

Any MCPO filesystem, shell or stdio tool executes inside the vendor container. It does not operate on the customer gateway. Only the tunneled `building-os` MCP backend operates the customer site.

## 13. Health and readiness

### 13.1 Gateway internal readiness

- active site ID is valid, bound and has starter or router-minted provenance;
- FastMCP answers health on the site-ID port;
- FastMCP catalogue proof matches the expected BOS catalogue;
- BOS gateway on port `7085` is reachable.

A starter-only site stops at internal readiness and never reports public-tooling readiness.

### 13.2 Gateway public-tooling readiness

- gateway internal readiness passes;
- the active ID has router-minted provenance;
- the MikroTik hardware binding matches enrollment;
- tunnel authentication succeeds;
- a recent poll has completed.

### 13.3 Site application readiness

- configuration loaded;
- the configured external OAuth authority and site resource binding are exact;
- a valid token for this site is accepted and a token for another site is rejected;
- no registration, authorization, consent or token-issuance route is active in the site MCPO component;
- the page/session component is healthy and its private state boundary passes its required write-read check;
- internal tunnel credential resolves to the assigned site only;
- BOS tool discovery succeeds through the tunnel;
- the discovered catalogue belongs to the expected site.

### 13.4 Vendor readiness

- SQLite accepts transactional writes;
- no active public-tooling site has a duplicate site ID or host port;
- request leases can be acquired and recovered;
- container desired and actual state is reconciled;
- the shared HTTPS edge routes each site hostname and `/mcp`, `/ui` and `/session` path to the recorded loopback origin;
- the edge configuration contains no OAuth client, grant, token or browser-session state;
- audit records can be written without storing secrets.

Readiness reports the failed layer. It does not report a generic healthy state while a required dependency is unavailable.

## 14. Wargame matrix

| Drill | Pass condition |
| --- | --- |
| Two active public-tooling sites | Each has a unique site ID, port, container, state volume, hostname and tunnel |
| Starter-only no-MikroTik site | Starter ID remains stable, internal FastMCP works, and no public hostname, container or tunnel is activated |
| Public tooling requested without MikroTik | Provisioning stops before router-minted allocation, enrollment, route or container activation |
| Starter-ID collision, pending-ID race or occupied port | Injected random candidates and insert-time unique conflicts count against the 1,000-draw limit; no duplicate becomes active and every successfully inserted candidate abandoned after a port race is retained as retired |
| Router-minted ID equals starter ID | Provenance, hardware binding and audit change without changing spatial IDs or the FastMCP port |
| Router-minted ID conflicts with another local identity | Gateway rejects it and the vendor reserves the next deterministic candidate; the other site is untouched |
| Re-key failure before cutover | Staged work is discarded, the reservation is released, starter FastMCP remains active and no public asset exists |
| Re-key interruption after cutover starts | Site enters `recovery_required`, public activation remains blocked and journal recovery reaches one verified local identity |
| Crash at each durable re-key boundary | Inject a crash after the SQLite commit, package promotion, device activation, RouterOS activation, listener stop and listener start; each run reaches one verified identity through the documented forward recovery or explicit rollback path |
| Re-key with current immediate-only device firmware | A site with commissioned spatial IDs fails preflight; no device is rewritten |
| Re-key with prepared-device protocol | Every device prepares, activates and reports the same transition ID before `router_minted_local` |
| Offline device or required site-link peer | Cutover does not begin; the starter remains active |
| Injected SQLite migration failure | The immediate transaction rolls back and `PRAGMA foreign_key_check` remains empty |
| Injected package promotion failure | The journal retains the old verified package and active pointer or resumes the staged promotion; public activation remains blocked |
| Derived subnet and peer-route change | Local and peer RouterOS state verifies against the new ID before local commit |
| Reuse of retired starter ID | Gateway allocation rejects the retired ID permanently |
| Cross-site OAuth token | The separate OAuth service binds the token to site A and site B MCPO and page/session components reject it |
| Cross-site backend request | Site A container cannot address or select site B tunnel |
| Site marker tools | Site A can discover only A's marker and site B only B's marker |
| Provider projection | Only the explicitly proposed `vendor-site` tools and frozen `building-os` allowlist are discoverable |
| Vendor-local tool execution | Swipe-card, upload, inspect and finalise calls create no tooling-tunnel request |
| Population orchestration | Confirmed population emits only the allowlisted gateway import and verification calls |
| Reduced five-digit candidate collision | Transaction allocates the deterministic next candidate and stores the accepted attempt |
| Conflicting full hardware MAC hash | Enrollment fails closed unless it is idempotently the same bound hardware record; five-digit candidate retry is not used |
| Occupied vendor port | Provisioning stops before route and container become active |
| Occupied router-minted gateway port | Gateway rejects the candidate before migration cutover and the vendor reserves the next deterministic candidate |
| Incorrect Cloudflare origin | Reconciliation detects the hostname-to-port mismatch before activation |
| MCPO process restart | A still-valid external OAuth token opens a new MCP session without local OAuth state or reauthorization |
| Site container replacement | Same hostname, host port, external grant and permitted browser-session persistence remain valid |
| Gateway tunnel drop | In-flight call returns a bounded failure or unknown result; later call succeeds after reconnect |
| Vendor restart with queued request | Undispatched request is recovered within its original deadline |
| Vendor restart after dispatch | Request becomes unknown and is not replayed |
| External OAuth validation unavailable | The site components follow the frozen fail-closed validation contract and do not fall back to local authorization |
| OAuth revocation | The external grant stops MCP and protected-page access within the frozen validation-cache bound |
| Site container auth surface | No dynamic registration, authorization, consent or token-issuance endpoint is exposed by MCPO |
| Browser session reopen | An authorized page reopens for the permitted session lifetime without using the tooling tunnel |
| Browser session revocation | The revoked session can no longer open `/ui` while unrelated site sessions remain active |
| Swipe-card visibility | Raw card is absent from ordinary tool content, model transcript, URLs, redirects, page markup and logs |
| Swipe-card exchange | Trusted component POST succeeds once, returns a clean `303`, sets the cookie in the page context and rejects replay |
| Missing component metadata support | Handoff fails closed with no query-token or model-visible URL fallback |
| Page path isolation | `/ui`, `/session`, HTML, assets and cookies never appear in the tooling-tunnel queue or gateway requests |
| Shared edge inspection | Edge configuration contains only TLS and hostname/path routing, not OAuth, grants, sessions or tunnel queue logic |
| Tunnel revocation | Gateway polling and response posting stop |
| Public MAC mismatch | Gateway tunnel startup stops pending explicit enrollment action |
| Compromised site container | No Docker socket, other site volume, other site network or host filesystem is reachable |
| Image inspection | No runtime token, OAuth state, provider secret or site configuration is embedded |
| External acceptance | Real ChatGPT proves the proposed vendor tools, one-use swipe-card flow, protected page and confirmed ReCAD population through the public hostname |

## 15. Build stages

### Stage 1: starter identity, public re-key and direct-port runtime

- implement the frozen `starter-random-v1` allocator, `site_identity_allocations` ledger, pending-to-active gate and retired-ID check;
- make the allocator the only new-site creation path; `POST /api/sites` and the TUI import flow must not accept or forward a caller-selected new `site_id`;
- add explicit site provenance and the `site_identity_transitions` recovery journal;
- implement one immediate, deferred-foreign-key registry transaction for the typed database migration and require an empty foreign-key check;
- implement staged site-package, active-pointer, filesystem-registry and cache migration;
- implement the prepared-device identity protocol before allowing commissioned-site re-key;
- migrate derived RouterOS subnet and site-link state, then rebuild phonebooks, automations and ledgers;
- split vendor identity reservation from site, tunnel, site-container, external-OAuth, access-key and route activation;
- freeze the canonical RouterOS MAC property and normalization;
- replace serial as the identity and custody key across the router record, `adoptVendorSiteBinding`, enrollment credential persistence and restart verification with the canonical MAC hash; serial may remain only as non-authoritative inventory metadata;
- change five-digit router-minted derivation input to the canonical normalized MikroTik MAC;
- preserve `10000..65535`, collision checking and deterministic retry;
- generate the vendor-returned `site_id` from the same canonical MAC derivation, or validate it against that derivation before the gateway accepts and persists it;
- parameterize FastMCP to bind the allocated site-ID port;
- remove the extra forwarding hop from the target startup path;
- update service installation, health checks and tests.

Pass gate:

- one site without a MikroTik receives a stable starter ID, operates internally and cannot report public-tooling readiness;
- injected starter candidates prove collision, occupied-port, exhaustion and retired-ID handling;
- the API and TUI new-site paths allocate and return the starter ID and cannot persist a caller-selected ID;
- one real gateway derives the expected site ID from its canonical MAC;
- gateway restart re-verifies the persisted enrollment against the same canonical MAC hash, not the RouterOS serial;
- one equal-ID transition changes provenance without moving the port or spatial IDs;
- one populated starter site migrates its complete typed database and package surface with no old active references and an empty foreign-key check;
- one commissioned starter site prepares and activates every device under one transition ID;
- an injected pre-cutover failure leaves the starter active; crash injection after each SQLite, package, device, RouterOS and listener durable boundary reaches one verified identity from the journal with no public asset;
- a mismatched vendor-returned site ID is rejected before public re-key cutover and tunnel startup;
- no vendor listener, tunnel, container, external authorization binding, access key or route exists before `router_minted_local`;
- FastMCP alone owns `127.0.0.1:<active_site_id>` in steady state;
- a direct authenticated tool call succeeds on that port;
- port and collision tests pass.

### Stage 2: BOS-compatible tunnel control plane

- pin the reviewed OpenAI tunnel client version;
- implement metadata, poll and response endpoints;
- implement internal site MCP ingress;
- implement durable SQLite queue, leases, deadlines and unknown-result handling;
- bind each MCPO credential to one opaque tunnel;
- strip non-local authorization headers before gateway dispatch;
- reject `/ui`, `/session`, HTML, asset, cookie and model traffic at the tunnel boundary.

Pass gate:

- one local MCPO client calls the gateway FastMCP through the control plane;
- tunnel disconnect and vendor restart drills preserve non-replay rules;
- a non-MCP page or session request cannot enter the tunnel queue.

### Stage 3: separate OAuth service

- deploy the vendor OAuth authority as a service separate from the shared edge, site containers and tooling tunnel;
- bind users, clients, grants and tokens to explicit site resources;
- implement the ChatGPT-facing authorization, refresh and revocation contract;
- define the site-container token-validation contract and maximum revocation-cache delay;
- keep signing or introspection credentials outside site container images;
- add site-wide grant revocation and customer/operator authorization controls;
- add endpoint abuse controls and durable OAuth state.

Pass gate:

- one ChatGPT client authorizes once and refreshes against the separate authority;
- a site-A token cannot authorize site B;
- site-wide revocation stops every token for that site within the frozen bound;
- authority restart preserves valid grants and tokens;
- no edge, site-container or tunnel database contains the authority's end-user OAuth state.

### Stage 4: hardened per-site application image

- configure MCPO to validate the external site's token contract without minting or persisting end-user OAuth tokens;
- disable local `/register`, `/authorize`, `/consent` and `/token` routes;
- aggregate the local `vendor-site` provider with the one allowlisted tunneled `building-os` backend;
- implement the proposed vendor tools `bos_bound_site_context`, `bos_get_swipe_card`, `bos_recad_upload`, `bos_recad_inspect`, `bos_recad_finalise_proposal` and `bos_site_populate_from_recad`;
- enforce local-only execution for session, artifact, inspection and finalisation work;
- enforce the fixed gateway read, import and verification allowlist for orchestration;
- package separate `/mcp` and `/ui` plus `/session` components inside the isolated prototype container;
- implement the one-use, component-metadata-only swipe card, POST exchange, clean `303`, expiry, persistence and revocation contract with no query-token fallback;
- keep `/config` read-only and page/session state private and writable;
- remove runtime files and credentials from the image build context;
- add non-root user, container restrictions, liveness and readiness;
- test wrong-site tokens, absent local OAuth state, protected page access and browser-session revocation.

Pass gate:

- image inspection finds no site state, end-user OAuth state or token-minting key;
- a valid site token reaches only its assigned `/mcp` tools;
- a wrong-site token is rejected by both MCPO and page/session components;
- the exact proposed vendor tool set and allowlisted gateway projection are the only published tools;
- vendor-local tool execution creates no tooling-tunnel request;
- ordinary tool content and URLs contain no raw card, and a replayed card fails after the first POST exchange;
- a container restart needs no local OAuth recovery and meets the frozen browser-session persistence contract;
- page-session revocation stops the protected page without affecting another site.

### Stage 5: supervisor and shared-edge reconciliation

- implement site deployment records;
- create and replace containers;
- bind host loopback site ports;
- drive the shared HTTPS edge and existing Cloudflare route method;
- route each site's `/mcp`, `/ui` and `/session` paths to that site's one private container origin;
- keep TLS and hostname/path routing as the edge's only responsibilities;
- compare route, path, port, hostname and container state continuously;
- implement site suspension and revocation.

Pass gate:

- two sites provision independently;
- deliberate route and port faults are detected;
- stopping or replacing one site does not disturb the other;
- edge configuration inspection finds no OAuth, grant, browser-session or tunnel-queue ownership.

### Stage 6: public acceptance

- begin with a real gateway carrying a starter site ID;
- enroll its MikroTik from the canonical MAC and complete the controlled router-minted re-key;
- provision the real site hostname and container;
- register the site resource in the separate OAuth service;
- authorize real ChatGPT once;
- call `bos_bound_site_context` and prove the vendor, grant and gateway site bindings match;
- call `bos_get_swipe_card` and live-prove non-model-visible component metadata, one POST exchange, a clean `303` and protected page access;
- run `bos_recad_upload`, `bos_recad_inspect` and `bos_recad_finalise_proposal` without a tunnel request;
- run one confirmed `bos_site_populate_from_recad` and verify the allowlisted gateway import and read-back calls;
- drop and restore the gateway tunnel;
- restart and replace the site container;
- repeat from another authorized client installation;
- inspect logs for secret and payload leakage;
- inspect model-visible results, browser history, redirects and logs for raw-card leakage;
- inspect tunnel records to prove no vendor-local ReCAD, page, session, card, asset or cookie traffic crossed it.

Pass gate:

- the entire public-to-device chain works;
- the protected page works through the edge-to-site-container path;
- the swipe card is absent from model-visible content and every URL, succeeds once through POST and redirects cleanly;
- upload, inspect and finalise remain vendor-local while confirmed population uses only the frozen gateway allowlist;
- reconnects do not require new OAuth authorization while the external grant remains valid;
- OAuth, page-session, tunnel and cross-site revocation drills pass;
- isolation and non-replay drills pass.

### Stage 7: optional inference

This begins only after the tool path passes public acceptance.

- choose supported upstream model providers;
- store provider credentials per allowed tenancy boundary;
- add separate model authorization, budgets and usage records;
- expose the required completion endpoint separately from MCP OAuth;
- prove model access cannot expand site tool authority.

Pass gate:

- provider calls are metered and attributable;
- tool-only customers do not receive model access;
- model credentials never enter the gateway tunnel.

## 16. Definition of working

The vendor add-on is working only when all of these are true:

- every site receives a valid five-digit starter ID with recorded provenance;
- a site without a MikroTik can operate internally without being exposed as public-ready;
- a canonical MikroTik MAC deterministically maps to one collision-checked five-digit site ID;
- public tooling activates only after the active site ID is router-minted and any starter re-key has completed without mixed identity state;
- the gateway FastMCP service listens directly on the current active site-ID port;
- a public-tooling gateway maintains the outbound tunnel without an inbound customer-network port;
- one isolated site application container serves `/mcp`, `/ui` and `/session` for each public-tooling site hostname;
- the shared public edge terminates TLS and routes hostnames and paths only;
- the separate OAuth service owns users, clients, site grants, token issuance, refresh and revocation;
- MCPO validates externally issued site-scoped tokens and stores no end-user OAuth state;
- for a public-tooling site, ChatGPT completes OAuth once and reconnects while the external grant remains valid;
- MCPO aggregates the container-local `vendor-site` provider with exactly one allowlisted tunneled `building-os` backend;
- vendor-local swipe-card, session, ReCAD artifact, inspection and finalisation work never enters the tooling tunnel;
- the one-use swipe card reaches only trusted component metadata, is exchanged by POST and never appears in ordinary model content or a URL;
- for a public-tooling site, MCPO can discover and call only the assigned site's BOS tools;
- public tool execution reaches the BOS gateway and building devices;
- protected pages and browser sessions remain vendor-side and never enter the gateway tooling tunnel;
- the tooling tunnel transports gateway MCP tool requests and responses only;
- public-stack restarts, drops and replacements do not replay uncertain writes;
- public access revocation stops access at the intended boundary;
- two public-tooling sites operate simultaneously without credential, state, network or tool crossover;
- failures identify the broken layer and fail closed.

## 17. Items that must be frozen before implementation

The starter allocator, provenance, equal-ID behavior, re-key state machine, migration surface, port cutover, failure recovery and old-ID disposition are frozen in Section 4. The remaining unresolved contracts are:

- the exact RouterOS property used as the canonical unique MAC ID for the public replacement;
- the normalized public MAC byte and text representation;
- the exact public MAC digest-to-port reduction, including retry encoding;
- the internal MCPO-to-vendor ingress URL;
- queue lease and request deadline values;
- the existing Cloudflare route automation command or API boundary;
- the secret storage mechanism on the vendor host;
- the external OAuth issuer, site-resource, client-registration, token-validation and maximum revocation-cache contracts;
- the customer and operator grant-revocation interface;
- the exact AI-host component-metadata API used to deliver the non-model-visible swipe card;
- the browser-session cookie, persistence, expiry and revocation contract after the fixed one-use POST exchange and clean `303`;
- the exact model-visible gateway read allowlist and provider-internal import and verification allowlist;
- the first safe read-only tool used for external acceptance.
