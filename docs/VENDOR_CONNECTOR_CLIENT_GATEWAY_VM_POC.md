# Vendor Connector and Client Gateway VM POC

Status: planned separate-machine POC, based on verified same-machine components.

Date: 2026-07-23.

## 1. Scope

This record defines the next proof of concept:

- keep the BOS/vendor server and development work on the main Windows machine;
- keep the server-side container on that machine;
- use a separate Windows VM as the client gateway;
- reproduce the machine and network separation expected at an onsite Mac Mini;
- keep all client access behind the outbound tooling tunnel;
- do not expose an inbound client-site port, LAN route, or VPN.

The VM is not claimed to exist or be configured. Private server-side ingress,
the separate external OAuth authority, public DNS, and the final deployment
layout are not claimed complete.

## 2. Product naming

Use these product terms:

| Term | Meaning |
| --- | --- |
| Vendor Connector | The server-side vendor add-on connection service built from the existing MCP proxy code lineage |
| Site Connector Container | One isolated, server-side Vendor Connector container assigned to one client site |
| Client Gateway | The onsite machine, represented in this POC by a separate Windows VM |
| OpenAI tunnel client | The outbound-only process on the Client Gateway |
| BOS FastMCP | The loopback MCP projection on the Client Gateway |
| BOS gateway | The client-site Building OS runtime and source of building truth |

`MCPO` is retained only where the repository, Python package, image,
configuration, environment variable, or compatibility surface still uses that
internal name. Do not use `client MCPO` as a product term. The Site Connector
Container runs on the server, not on the client machine.

The Site Connector Container and the OpenAI tunnel client are separate:

- the Site Connector Container is the isolated server-side boundary for one site;
- the OpenAI tunnel client is the client-side outbound transport agent;
- the vendor control plane maps the assigned site to one opaque tunnel.

## 3. Verified current state

The following state was recorded and verified on 2026-07-23:

- the source repository is on the main machine at
  `D:\vibe-coded-projects\mcpo`;
- Docker project `mcpo-site-44354` created a healthy POC container;
- the container listens on internal port `8351`;
- Docker publishes it on main-host loopback as
  `127.0.0.1:44354 -> 8351`;
- the vendor service remained separate on `127.0.0.2:44354`;
- the container resolved its Building OS backend as
  `https://44354.ai.lighting/mcp`;
- an MCP `initialize` POST from the container through that public Cloudflare
  hairpin returned HTTP `200` and Building OS Tooling protocol metadata;
- `44354.gateway.ai.lighting` did not resolve;
- the public OAuth route for the site-specific POC was therefore incomplete;
- the gateway FastMCP and native tunnel client were manual processes in the
  same-machine simulation;
- the official persistent gateway tasks, persistent local FastMCP key, and
  reboot recovery were not proved;
- `S:` was mounted as a healthy 200 GB server VHDX; the
  `S:\BOS-Vendor` layout was created and all 16 directories validated; the
  site secrets directory remained empty; automatic remount, secret ACLs,
  backup and rollback operation remain unproved.

This proves the current container, vendor broker, native tunnel client, and
gateway components. It does not prove the separate-machine POC.

## 4. Corrected machine roles

### 4.1 Main Windows machine

Role: BOS/vendor server, development host, and Docker host.

Keep on this machine:

- the `mcpo` source repository and development tools;
- the vendor control plane and site broker;
- the Site Connector Container for site `44354`;
- the shared public HTTPS and Cloudflare tunnel boundary;
- the current self-hosted OAuth POC only while it remains explicitly marked as
  a development proof;
- future separate external OAuth service;
- server deployment data, releases, secrets, state, logs, backups, and rollback
  records on `S:` once that layout is built.

The main machine is not the simulated client gateway after this split.

### 4.2 Client Gateway VM

Role: separate client machine simulating the onsite gateway host.

Place in the VM:

- the BOS gateway on loopback port `7085`;
- BOS FastMCP on loopback port `7087` for the current POC contract;
- the protected local FastMCP bearer;
- the protected vendor tunnel credential;
- the OpenAI tunnel client in explicit `openai-poll` mode;
- the official local MCP and hosted tunnel service wrappers;
- gateway-side logs and bounded status output.

Do not place in the VM:

- the Site Connector Container;
- the Vendor Connector server state;
- the vendor database or operator service;
- the public HTTPS edge;
- the OAuth authority;
- `S:` server storage;
- development repository state that is not required by the packaged gateway
  runtime.

The longer-term scratch architecture proposes moving BOS FastMCP from `7087`
to `127.0.0.1:<site_id>`. That change is outside this placement POC until its
gateway implementation and migration gates are separately approved and tested.

## 5. Request chains

### 5.1 Current recorded POC

```text
local client
  -> Site Connector Container on main-host loopback
  -> https://44354.ai.lighting/mcp
  -> public Cloudflare hairpin
  -> vendor site broker
  -> native tunnel client on the same machine
  -> BOS FastMCP on 127.0.0.1:7087
  -> BOS gateway on 127.0.0.1:7085
```

This chain is a same-machine simulation. The hairpin is deliberate POC debt,
not the production server path.

### 5.2 Separate-machine placement POC

```text
ChatGPT, Claude, or a controlled MCP test client
  -> public HTTPS and POC OAuth on the main machine
  -> Site Connector Container for site 44354
  -> current authenticated vendor site provider
  -> vendor poll broker on the main machine
  -> outbound poll held by the Client Gateway VM
  -> BOS FastMCP on VM loopback 127.0.0.1:7087
  -> BOS gateway on VM loopback 127.0.0.1:7085
  -> response returns through the same request and tunnel identity
```

The placement POC may retain the current public vendor hairpin while proving
the VM boundary. It must remain labelled as a POC.

### 5.3 Target product chain

```text
ChatGPT or Claude
  -> shared public HTTPS edge
  -> separate external OAuth authority
  -> Site Connector Container for the authorized site
  -> fixed private vendor ingress
  -> vendor maps the site to one opaque tunnel
  -> outbound OpenAI tunnel client on the onsite gateway
  -> BOS FastMCP on gateway loopback
  -> BOS gateway
```

The fixed private Vendor Connector-to-vendor ingress and separate external
OAuth contract are not built or proved. The target chain must not fall back
silently to the public hairpin.

## 6. Network boundary

Main-host rules:

- publish each Site Connector Container only on its assigned main-host loopback
  port;
- keep the container internal listener on `8351`;
- expose public traffic only through the shared HTTPS edge;
- bind one Site Connector Container to one site and one tunnel;
- do not allow caller-selected tunnel IDs, gateway URLs, or raw gateway tool
  names;
- replace the public provider hairpin with fixed private ingress before calling
  the server path production-ready.

Client Gateway VM rules:

- initiate the tunnel connection outbound;
- expose no inbound client-site port;
- bind BOS FastMCP and the BOS gateway to VM loopback only;
- carry only bounded, allowlisted gateway MCP requests and responses;
- do not forward public OAuth or Site Connector authorization headers into the
  gateway;
- do not create a standing LAN route, subnet exposure, or VPN.

The Site Connector Container is the per-site server sandbox. The target
container contract requires a non-root user, read-only root filesystem,
read-only generated configuration, dropped capabilities, no Docker socket, no
host or customer filesystem mounts, bounded resources, and a site-specific
network. These controls still require runtime inspection and negative tests on
the deployed Site Connector image.

## 7. Storage boundary

### 7.1 `S:` server storage

Reserve `S:` for server-side data only:

- immutable Vendor Connector releases and image provenance;
- per-site generated configuration;
- server-side secret mounts;
- Compose and supervisor state;
- vendor database state;
- logs and bounded audit records;
- encrypted backups;
- rollback manifests.

The fail-closed initializer created `S:\BOS-Vendor` and the site `44354`
subdirectories on the real drive. The validator confirmed all 16 directories
and the site secrets directory contained no entries. Secret ACL enforcement,
backup operation, rollback operation, and automatic remount remain unimplemented.

Do not store the Client Gateway VM disk, client credentials, or client runtime
state on `S:`.

### 7.2 `D:` development and VM storage

`D:` currently holds the development repository. The fixed VM root is
`D:\BOS-Client-Gateway-VM`. Oracle VirtualBox `7.2.12r174389` is installed and
its host drivers are running. The prepared definition uses one 80 GiB VDI
beneath that root. No VM or disk exists yet because a verified Windows 11 ISO
has not been supplied and the creation script has not been run.

The VM guest keeps its own client runtime state and must not mount `S:`. The
creation and validation scripts are under `deploy/client-gateway-vm/`.

## 8. Secret boundaries

Keep these credentials separate:

| Credential | Owner and location |
| --- | --- |
| External OAuth grant and token | Separate OAuth authority and authorized client; target only |
| Site Connector internal ingress key | Main server; one site and one private vendor ingress |
| Vendor site-access key | Main server POC; one site provider |
| Gateway tunnel runtime key | Client Gateway protected credential store; one opaque tunnel |
| Local FastMCP bearer | Client Gateway protected local store; loopback MCP only |
| BOS gateway API key | Client Gateway; FastMCP-to-gateway boundary only |
| Cloudflare tunnel credential | Main server edge service only |

Rules:

- never place secrets in Git, images, Compose output, diary text, commands, or
  ordinary status output;
- do not reuse one credential across boundaries;
- pass the local FastMCP bearer to the native client without putting it in argv;
- remove public authorization headers before a request enters the tooling
  tunnel;
- mount only the secret required by the assigned process.

The current ignored POC environment file is not the target secret store. Any
credential displayed or copied outside its intended protected store must be
rotated before reuse. No credential value is reproduced here.

## 9. Startup and recovery

### 9.1 Main server target order

1. Mount and verify `S:`.
2. Start the vendor control plane and admin service.
3. Start Docker and verify the private server network.
4. Start the assigned Site Connector Container.
5. Verify site binding, container health, and backend identity.
6. Start or verify the shared HTTPS edge.
7. Enable the public route only after the required private and authorization
   checks pass.

The server directory layout is initialized and validated. `S:` automatic
remount, secret ACLs, backup and rollback operation, private ingress, route
reconciliation, and the external OAuth startup path are not implemented.

### 9.2 Client Gateway VM target order

1. Start the BOS gateway and verify `127.0.0.1:7085`.
2. Load the protected local FastMCP key.
3. Start `BuildingOS-LocalMCP` and verify `127.0.0.1:7087`.
4. Load the protected `openai-poll` tunnel credential.
5. Start `BuildingOS-HostedTunnel` through the official runner.
6. Require tunnel metadata, poll freshness, local MCP health, and process-tree
   ownership before reporting ready.

The recorded same-machine FastMCP and native tunnel client were manual. The
official tasks, persistent keys, VM boot startup, restart, and recovery are not
proved.

## 10. Existing verified gates

These results were recorded before the VM split:

- `uv run pytest -q`: 640 passed, 4 skipped;
- focused configuration and OAuth tests: 77 passed;
- gateway POC Compose render: passed;
- Site Connector lineage container: healthy;
- invalid site-ID startup rejection: passed;
- in-container backend MCP initialize through the public hairpin: HTTP `200`;
- vendor tests: 94 passed with 1 optional integration skip;
- native tunnel client source verification: 453 of 453 files;
- native binary version and SHA-256 verification: passed;
- targeted host tunnel tests: 28 passed.

These gates prove the recorded components. They do not prove VM separation,
private ingress, external OAuth, or macOS deployment.

## 11. Placement POC acceptance gates

The separate-machine POC passes only when all of these checks pass:

1. The Client Gateway VM starts from a clean boot without development-shell
   commands.
2. BOS gateway, BOS FastMCP, and the OpenAI tunnel client start through their
   intended service wrappers.
3. The tunnel client makes only outbound control-plane connections.
4. A network scan proves no inbound gateway service is exposed outside the VM.
5. The main-host Site Connector Container remains on the main machine and no
   Vendor Connector container exists in the VM.
6. One authenticated MCP read traverses the Site Connector Container, vendor
   broker, VM tunnel client, BOS FastMCP, and BOS gateway.
7. The returned site identity is `44354` and a wrong-site credential or tunnel
   binding fails closed.
8. Public and server-side authorization headers are absent from the gateway
   request.
9. An interrupted or timed-out write is not replayed automatically.
10. Restarting the VM restores the local MCP and hosted tunnel services without
    manually entering secrets.
11. Restarting the Site Connector Container does not require rebuilding the
    Client Gateway VM.
12. Logs, status, process arguments, child environment, Compose output, and
    generated artifacts contain no plaintext credential.
13. The VM does not mount or write `S:`.

## 12. Target product gates

The product target remains incomplete until:

- the public hairpin is replaced with fixed private Vendor Connector ingress;
- the separate external OAuth authority issues and validates site-scoped grants;
- `44354.gateway.ai.lighting` or the approved replacement hostname resolves and
  routes only to the assigned Site Connector Container;
- the hardened container contract passes runtime inspection and cross-site
  negative tests;
- `S:` has a verified permission, backup, restore, and rollback layout;
- server and client restart drills pass;
- a real ChatGPT or Claude connector completes authorization and an MCP call;
- two-site credential, network, state, and tool isolation pass;
- revocation stops the intended site without disturbing another site.

## 13. Mac Mini parity limitation

The Windows VM can prove:

- a separate clean operating-system instance;
- no shared localhost with the main server;
- outbound-only client connectivity;
- packaged startup and restart behavior;
- separation of server and client credentials;
- loss and reconnection behavior.

It cannot prove:

- macOS LaunchDaemon or login-item behavior;
- Keychain storage and access controls;
- macOS firewall and network-extension behavior;
- macOS Docker or container networking;
- binary signing, notarization, quarantine, or Gatekeeper behavior;
- Apple filesystem ownership and permission behavior;
- the final Mac Mini installer and upgrade path.

Run a separate Mac Mini acceptance pass before describing the client gateway as
macOS-ready.

## 14. Build artifacts

- `deploy/client-gateway-vm/New-ClientGatewayVm.ps1`: prepares a powered-off
  Windows 11 VirtualBox definition at `D:\BOS-Client-Gateway-VM`.
- `deploy/client-gateway-vm/Test-ClientGatewayVm.ps1`: rejects VM resource,
  networking, sharing, USB, disk, ISO, or powered-state drift.
- `deploy/vendor-server/Initialize-VendorServerStorage.ps1`: creates only the
  approved `S:\BOS-Vendor` directory layout for one validated site ID.
- `deploy/vendor-server/Test-VendorServerStorage.ps1`: validates that layout
  without filesystem writes.
- `docker-compose.gateway-poc.yml`: now declares Vendor Connector role labels
  and future one-CPU, `512m` memory-limit, and `128m` reservation settings.
  Those limits are not active on the currently running container until a
  controlled recreation and runtime reinspection.

No repository script installs VirtualBox, downloads Windows, starts the VM,
creates a secret, or recreates the live container. VirtualBox was installed
separately through Winget after explicit approval. The storage initializer was
run once against the real `S:` drive and its read-only validator passed.

## 15. Evidence records

- `.claude/diary-2026-07-23.md`
- `docs/scratch-vendor-addon-mcpo-container-stack.md`
- `docs/OAUTH_TUNNEL_SETUP.md`
- `C:\Users\danie\building-os\jobs\26-07-23\client-gateway-mcpo-tunnel-current-state-and-finish-report.md`
- `C:\Users\danie\building-os\jobs\26-07-23\scratch-openai-tunnel-client-integration.md`
