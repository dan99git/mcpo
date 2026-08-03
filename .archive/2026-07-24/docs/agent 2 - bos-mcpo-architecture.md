# Agent 2: BOS-MCPO Architecture

Date: 2026-07-23  
Status: context-derived architecture record  
Scope: BOS Vendor Connector, MCPO, site containers, OpenAI tunnel, Cloudflare publication, client gateway, OAuth, model API, keys, storage, and the Windows VM proof of concept

## 1. Evidence boundary

This document was written from the current conversation context and prior Codex memory notes.

No repository source code, configuration, Compose file, deployment script, runtime log, or existing project document was inspected while writing it.

Statements are classified as:

- **Settled**: explicitly decided during the working conversation.
- **Previously verified**: a command, test, or runtime result was recorded earlier in the conversation.
- **Planned**: the intended production or proof-of-concept target.
- **Unknown**: not established by the available context and requires fresh inspection or testing.

This is an architecture snapshot, not a fresh source audit.

## 2. System purpose

The system gives ChatGPT, Codex, and other approved clients controlled access to Building OS tools at a customer site without opening an inbound port on the customer network.

The design has two separate connection problems:

1. Publish a protected vendor endpoint that ChatGPT can reach.
2. Carry each approved tool request from the vendor service to the correct onsite gateway through an outbound client connection.

Cloudflare publication and the OpenAI tunnel do different jobs. They are not interchangeable.

## 3. Settled product names

| Name | Meaning |
|---|---|
| **BOS Vendor Server** | The vendor-side Windows and Docker host. In the current POC this is the main development machine. |
| **Vendor Connector** | The server-side BOS product role assembled from MCPO lineage plus BOS-specific routing, isolation, tunnel, security, and operations. |
| **Site Connector Container** | One isolated server-side container for one customer site. |
| **MCPO** | The internal code lineage and compatibility layer. It is not the final product name for the per-site service. |
| **Client Gateway** | The onsite computer connected to the building network. The target field device is a Mac Mini. The POC uses a separate Windows VM. |
| **OpenAI tunnel client** | The client-side transport process that connects the Client Gateway to the vendor service using outbound HTTPS. |
| **Vendor tunnel broker** | The vendor-side service that accepts client polling, holds bounded work, correlates responses, and connects public requests to the correct gateway. |
| **General tooling MCPO** | The separate shared tooling instance associated with `dev.ai.lighting`. It must not be mixed with site gateway experiments. |

`MCPO` is poor external naming for the connector because the BOS role is broader than the original proxy. Product documentation should use `Vendor Connector` and `Site Connector Container`.

## 4. Host placement

### 4.1 BOS Vendor Server

**Settled**

- The main Windows development machine remains the BOS Vendor Server and Docker host.
- The Site Connector Container remains on this machine.
- Server deployment data belongs on `S:`.
- The client test VM belongs on `D:`, not `S:`.

**Previously verified**

- `S:` is a mounted 200 GB VHDX backed by `D:\AI-Lighting-Server\AI-Lighting-Server.vhdx`.
- `S:\BOS-Vendor` was created and validated with 16 expected directories.
- The site secrets directory was empty after initialization.

### 4.2 Client Gateway VM

**Settled**

- The VM represents a fresh client machine, not another process on the BOS Vendor Server.
- The VM reproduces separate machine, credential, loopback, restart, and network boundaries.
- The VM does not prove macOS parity.
- The Site Connector Container does not move into the VM.

**Planned VM contract**

- Name: `BOS-Client-Gateway`
- Host root: `D:\BOS-Client-Gateway-VM`
- Guest: Windows 11 x64
- Memory: 8 GiB
- CPUs: 4
- Disk: dynamic 80 GiB VDI
- Firmware: EFI
- TPM: 2.0
- Network: NAT only
- Port forwarding: none
- Additional adapters: none
- Shared folders: none
- Clipboard sharing: disabled
- Drag and drop: disabled
- USB: disabled
- Audio: disabled
- VRDE: disabled

**Previously verified**

- Oracle VirtualBox 7.2.12 was installed on the host.
- `VBoxManage` reported `7.2.12r174389`.
- No Windows ISO was available.
- No VM had been created or started.

## 5. Main request path

```text
ChatGPT or approved public client
        |
        | HTTPS plus OAuth
        v
Public site endpoint
44354.gateway.ai.lighting
        |
        v
Site Connector Container
host 127.0.0.1:44354 -> container 8351
        |
        | authenticated site-pinned backend request
        v
Vendor listener and tunnel broker
44354.ai.lighting/mcp
        |
        | queued and correlated tunnel work
        v
Outbound OpenAI tunnel client on Client Gateway
        |
        | private loopback request
        v
127.0.0.1:7087/mcp
Building OS local FastMCP tool server
        |
        v
Building OS gateway and building devices
```

The tool response returns through the same chain in reverse.

The current POC uses a public vendor hostname between the Site Connector Container and the vendor listener. Production should replace that public hairpin with fixed private vendor ingress.

## 6. Cloudflare role

Cloudflare publishes a vendor-side listener to the Internet.

It does not directly connect the customer gateway to the Site Connector Container.

The current known vendor route is:

```text
44354.ai.lighting
    -> Cloudflare Tunnel
    -> vendor-side listener
    -> 127.0.0.2:44354
```

Important distinctions:

- `44354.ai.lighting` is the vendor listener route.
- It must not be repurposed as the Site Connector Container hostname.
- `/mcp` is a URL path. It is not part of a DNS record name.
- A Cloudflare Tunnel DNS record normally points the hostname at the Cloudflare tunnel target.
- The customer network does not need inbound port forwarding for the outbound tunnel client.

The separate ChatGPT-facing Site Connector hostname is intended to be:

```text
44354.gateway.ai.lighting
```

Earlier context recorded that this hostname did not resolve. Its public DNS and route remain external work unless completed after this snapshot.

## 7. OpenAI tunnel role

The OpenAI tunnel client is the gateway-to-vendor connection.

It runs on the Client Gateway and initiates outbound HTTPS. That is why the customer firewall does not need a public inbound rule or port forward.

The broker model remembered from the reviewed protocol uses:

```text
GET  /v1/tunnels/{tunnel_id}
GET  /v1/tunnels/{tunnel_id}/poll
POST /v1/tunnels/{tunnel_id}/response
```

The intended sequence is:

1. The client authenticates to the vendor service.
2. The client polls for work.
3. The broker returns one bounded request for that site and tunnel.
4. The client forwards the request to the private local MCP server.
5. The client posts the correlated response to the broker.
6. The vendor request is completed.

The OpenAI tunnel client is transport. It is not the vendor control plane, public OAuth server, tenant database, or production queue by itself.

## 8. Site identity and port contract

**Settled**

- `BOS_VENDOR_SITE_ID` is the canonical site identity for this deployment shape.
- It must contain exactly five decimal digits.
- Its valid range is `10000` through `65535`.
- The current test site is `44354`.
- The host-published Site Connector port matches the site ID.
- The container-internal MCPO port remains fixed at `8351`.

Current mapping:

```text
BOS_VENDOR_SITE_ID=44354
127.0.0.1:44354 -> Site Connector Container:8351
```

The temporary port `8352` was removed from the active gateway experiment.

The host can use the same numeric port on different loopback addresses:

```text
127.0.0.1:44354  Site Connector Container publication
127.0.0.2:44354  vendor listener
```

These are separate sockets and separate roles.

## 9. Known ports and endpoints

| Port or endpoint | Remembered role | Status |
|---|---|---|
| `8000` | MCPO administrative, OpenAPI, model, or management surface | Role must be freshly verified before operational use. |
| `8001` | Local native MCP aggregation surface | Memory records parity with 8351 for tools and server state. |
| `7085` | Building OS gateway-side service observed in the POC | Exact current responsibility requires verification. |
| `7087` | Client-local Building OS FastMCP endpoint at `/mcp` | Settled client target. Not public. |
| `7088`, `7089` | Vendor-side listeners observed during the POC | Exact current responsibility requires verification. |
| `8351` | Fixed internal MCPO OAuth/MCP container port | Settled. |
| `44354` | Site ID and published host port for the test site | Settled. |
| `https://dev.ai.lighting/mcp` | Separate general tooling MCPO endpoint | Must remain separate from the site connector experiment. |
| `https://44354.ai.lighting/mcp` | Vendor listener/backend route for site 44354 | Existing route in the POC context. |
| `https://44354.gateway.ai.lighting/mcp` | Intended public Site Connector MCP endpoint | Public DNS and routing were incomplete in the last recorded check. |

Port names alone are not architecture. The owning process, bind address, authentication boundary, and direction of travel must always be recorded together.

## 10. Client Gateway services

The remembered Windows client package uses:

- Local MCP server: `127.0.0.1:7087/mcp`
- Windows credential protection: DPAPI `CurrentUser`
- Scheduled Task: `BuildingOS-LocalMCP`
- Scheduled Task: `BuildingOS-HostedTunnel`
- Tunnel runner environment pointing `MCP_SERVER_URL` at the local 7087 endpoint

Remembered configuration inputs:

```text
BOS_VENDOR_RELAY_URL
BOS_VENDOR_SITE_NAME
BOS_VENDOR_ENROLLMENT_TOKEN
BOS_CHATGPT_MCP_PORT=7087
```

The tunnel client receives a vendor control-plane URL and opaque tunnel identity through enrollment. It must not expose the local MCP key to the public caller.

The future Mac Mini package requires separate acceptance for:

- `launchd` service management
- macOS Keychain credential storage
- Apple Silicon binaries
- firewall behavior
- sleep and wake recovery
- unattended restart behavior
- installer and upgrade behavior

The Windows VM proves the network and service boundary only.

## 11. Site Connector Container

The Site Connector Container is one isolated server-side runtime per customer site.

Its responsibilities are:

- Present the site's protected public MCP surface.
- Perform public OAuth handling for the current POC.
- Bind requests to one site identity.
- Aggregate or proxy the allowed MCP tool set.
- Authenticate its request to the vendor backend.
- Keep site state separate from other sites.
- Apply resource and process isolation.

It is not:

- The onsite tunnel client.
- The Building OS local tool server.
- The general `dev.ai.lighting` tooling service.
- A durable multi-tenant control plane.
- A production tenant database.
- A complete durable queue, lease, fleet, audit, or HA system.

**Previously verified POC state**

- Container: `mcpo-site-44354-gateway-mcpo-1`
- Image lineage: `mcpo-gateway-poc:local`
- Host publication: `127.0.0.1:44354`
- Container port: `8351`
- Health: healthy
- Root filesystem: read-only
- Process: non-root
- Linux capabilities: dropped
- `no-new-privileges`: enabled
- PID limit and temporary filesystem isolation: present

The Compose definition was updated with:

- product-role label
- runtime-role label
- lineage label
- site-ID label
- 1 CPU limit
- 512 MiB memory limit
- 128 MiB reservation

Those new labels and limits were not active on the already-running container because it had not been recreated.

## 12. Server storage

The settled server root is:

```text
S:\BOS-Vendor
```

Remembered layout:

```text
S:\BOS-Vendor
├── releases
├── compose
├── config
├── state
├── logs
├── backups
├── rollback
└── sites
    └── 44354
        ├── config
        ├── state
        ├── logs
        ├── backups
        ├── rollback
        └── secrets
```

**Previously verified**

- All 16 expected directories existed.
- The site secrets directory contained no entries.

**Not implemented in the last recorded state**

- Secret ACL enforcement
- Secret values
- Backup jobs
- Restore drill
- Rollback job
- Rollback drill
- Automatic VHDX remount assurance

## 13. Authentication boundaries

The architecture has separate credentials for separate trust boundaries.

### 13.1 Public MCP OAuth

Public ChatGPT or Codex access authenticates at the Site Connector endpoint.

The desktop OAuth flow uses an HTTP loopback callback on an ephemeral `127.0.0.1` port. Earlier work identified a consent-page Content Security Policy block against that callback and added a loopback allowance. This document does not freshly verify that fix.

### 13.2 Site Connector to vendor backend

The Site Connector uses a site-specific access key in an `Authorization: Bearer ...` header when calling the vendor backend.

The key value must not be written into architecture documents, logs, examples, or memory notes.

### 13.3 Tunnel client enrollment

The Client Gateway receives an enrollment credential and opaque tunnel identity.

On Windows, the credential is remembered as DPAPI `CurrentUser` protected data.

### 13.4 Local gateway MCP

The tunnel client injects the private local MCP bearer only after receiving and sanitizing the vendor request.

Public `Authorization`, cookie, forwarding, and proxy headers must not be passed through to the local gateway.

## 14. Model API and Codex OAuth

The model plane must stay separate from the MCP tooling plane.

The remembered requirement is:

- Accept or maintain Codex OAuth as an upstream model credential.
- Present an OpenAI-compatible API surface.
- Protect that surface with managed API keys.
- Provide key generation, storage, identification, and revocation.
- Reserve a settings-page key section for Codex OAuth.

The intended credential split is:

```text
Codex OAuth credential
    -> upstream model access

Managed BOS API key
    -> downstream client access to the OpenAI-compatible endpoint
```

The managed API key must be revocable without destroying or exposing the upstream Codex OAuth credential.

**Unknown**

- Whether the key generator, persistence, revoke path, and settings UI are complete.
- Whether API keys are stored hashed, encrypted, or as plaintext.
- Whether Codex OAuth refresh, expiry, and multi-account handling are complete.
- Which OpenAI-compatible model endpoints are currently implemented and tested.

These items require a separate source and runtime audit.

## 15. Tool and model traffic separation

The gateway tooling tunnel is for MCP tool traffic.

It must not carry:

- HTML pages
- static assets
- browser cookies
- arbitrary public proxy headers
- general model traffic
- unrelated vendor administration traffic

Optional model routing belongs in a separate model service and authorization plane.

MCPO can contribute proxying, MCP aggregation, OAuth experiments, REST/OpenAPI surfaces, and model-provider routing. Context memory does not establish it as a production inference host or managed-agent platform.

## 16. Broker safety rules

The vendor broker needs more than three URLs.

Load-bearing behavior includes:

- Site and tunnel ownership checks
- Correlation IDs
- Bounded request and response sizes
- Poll deadlines
- Request deadlines
- Lease ownership
- Duplicate-client prevention
- Rate limits
- Durable undispatched work
- Audit records
- Restart recovery
- Clear terminal states

Failure rule:

- An undispatched request can be recovered.
- A request dispatched without a confirmed result becomes `unknown`.
- An uncertain mutation must not be automatically replayed.

## 17. Multi-site production shape

The intended production unit is one Site Connector Container per site.

Each site requires independent:

- site ID
- host publication
- container process
- configuration
- OAuth state
- backend key
- tunnel identity
- logs
- state
- secrets
- backups
- rollback data
- resource limits

Shared vendor services may provide:

- public HTTPS edge
- customer identity
- site directory
- external OAuth
- enrollment
- durable tunnel queues
- leases
- fleet operations
- audit
- rate limiting
- health monitoring
- optional model routing

MCPO JSON state and in-memory runtime state are not substitutes for the shared durable services.

## 18. Proof already recorded

Earlier context records the following successful POC checks:

- The Site Connector Container was healthy on `127.0.0.1:44354 -> 8351`.
- Local OAuth metadata returned HTTP 200.
- Unauthenticated local `/mcp` returned HTTP 401.
- Invalid site ID `9999` was rejected at startup.
- The container resolved its backend to `https://44354.ai.lighting/mcp`.
- An authenticated JSON-RPC `initialize` sent from inside the container through the public vendor route returned HTTP 200.
- That response identified `Building OS Tooling` version `3.4.4` and MCP protocol `2025-06-18`.
- The repository test suite recorded `667 passed` and `4 skipped`.
- Deployment-asset tests recorded `27 passed`.

This proves the server-side container could reach the public vendor route and receive an MCP response.

It does not prove:

- a separate Client Gateway VM connected through the OpenAI tunnel
- a complete ChatGPT public OAuth flow on the site hostname
- a real remote customer network
- Mac Mini behavior
- production durability
- production multi-site isolation
- backup or disaster recovery

## 19. Current incomplete work

### 19.1 Client proof

- Obtain a verified Windows 11 ISO.
- Create and validate the isolated VM.
- Install Windows.
- Install the existing Building OS client package inside the guest.
- Enroll the tunnel credential.
- Reboot the VM.
- Confirm both client services recover.
- Prove one safe read from the public/vendor side to the VM's local MCP server and back.

### 19.2 Public endpoint

- Create and verify `44354.gateway.ai.lighting`.
- Route it only to the Site Connector publication.
- Complete public OAuth registration, consent, token exchange, and MCP initialization.
- Keep `44354.ai.lighting` assigned to the vendor listener.

### 19.3 Server hardening

- Replace the public vendor hairpin with private vendor ingress.
- Separate external production OAuth from the per-site runtime.
- Activate the declared container CPU, memory, and role labels through controlled recreation.
- Apply secret ACLs.
- Implement backup, restore, rollback, and recovery drills.
- Add durable broker queues, leases, audit, rate limits, and HA.

### 19.4 Model API

- Verify the OpenAI-compatible endpoint.
- Verify Codex OAuth refresh and storage.
- Verify API key generation and revocation.
- Verify the settings-page keys section.
- Prove keys can be revoked without exposing or destroying upstream OAuth state.

## 20. Architecture invariants

The following rules should not drift:

1. The Client Gateway opens the outbound tunnel. It does not accept public inbound traffic.
2. Cloudflare publishes vendor-side endpoints. It is not the gateway tunnel client.
3. The Site Connector Container runs on the BOS Vendor Server, not inside the Client Gateway VM.
4. One site gets one isolated Site Connector Container.
5. The five-digit site ID is the published host port.
6. Container port `8351` remains internal and fixed.
7. Client-local FastMCP remains on `127.0.0.1:7087/mcp`.
8. `44354.ai.lighting` remains the vendor listener route.
9. `44354.gateway.ai.lighting` is the separate ChatGPT-facing Site Connector route.
10. General tooling at `dev.ai.lighting` remains separate.
11. Public credentials are stripped before local dispatch.
12. The private local MCP key is injected only on the client side.
13. Tool tunnel traffic remains separate from pages, cookies, assets, and model traffic.
14. Uncertain mutations are never automatically replayed.
15. MCPO lineage is a component source, not the complete vendor control plane.

## 21. Fresh verification required

Before using this document as an operational runbook, verify:

- current process ownership for ports `7085`, `7088`, `7089`, `8000`, `8001`, `8351`, and `44354`
- current DNS for both site hostnames
- current Cloudflare tunnel ingress rules
- current Compose state and live resource limits
- current public OAuth behavior
- current broker persistence and failure semantics
- current model API and key-management implementation
- current VM and Windows ISO state
- current secret ACL, backup, and rollback state

Those checks were deliberately not performed for this context-only document.
