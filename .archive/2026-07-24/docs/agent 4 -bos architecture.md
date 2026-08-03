      1 +# Agent 2 BOS-MCPO architecture context record
      2 +
      3 +Date: 2026-07-23
      4 +
      5 +## 1. Source boundary
      6 +
      7 +This document records the architecture understood from the current conversation and pri
         or context memory.
      8 +
      9 +No repository source code, configuration, Docker state, DNS, or live service was inspec
         ted while writing it.
     10 +
     11 +The evidence labels used below are:
     12 +
     13 +- **Settled decision:** explicitly decided during the architecture work.
     14 +- **Context-reported verified:** previously reported as checked by a command, test, or
         runtime probe, but not rechecked for this document.
     15 +- **Planned:** intended design that is not yet proven end to end.
     16 +- **Unknown:** not established by the available context.
     17 +
     18 +This is an architecture handover, not current-state proof. Live state must be checked b
         efore deployment or destructive work.
     19 +
     20 +## 2. System purpose
     21 +
     22 +BOS-MCPO provides a controlled path between an OpenAI or ChatGPT MCP client and the too
         ls running on a remote Building OS gateway.
     23 +
     24 +The client site does not expose an inbound port. The client-side connector opens outbou
         nd HTTPS to the vendor service. The vendor side accepts the public request, associates
         it with one site, sends it through the existing outbound connection, and returns the re
         sult.
     25 +
     26 +MCPO supplies useful OAuth, MCP aggregation, OpenAI-compatible API, provider-routing, a
         nd proxy components. It is not the complete BOS vendor control plane.
     27 +
     28 +## 3. Product names and roles
     29 +
     30 +### 3.1 Vendor Connector
     31 +
     32 +**Settled decision**
     33 +
     34 +`Vendor Connector` is the server-side product role built from MCPO code lineage.
     35 +
     36 +It owns the public or vendor-facing MCP boundary for a site. It is not installed on the
          client gateway.
     37 +
     38 +### 3.2 Site Connector Container
     39 +
     40 +**Settled decision**
     41 +
     42 +A `Site Connector Container` is one isolated Vendor Connector runtime for one customer
         site.
     43 +
     44 +The current POC uses one container for site `44354`. Production orchestration should pr
         eserve one-site isolation rather than combining unrelated customers in one mutable proc
         ess.
     45 +
     46 +### 3.3 Client Gateway
     47 +
     48 +**Settled decision**
     49 +
     50 +The `Client Gateway` is the machine at the customer site. It runs:
     51 +
     52 +- the Building OS gateway runtime;
     53 +- local FastMCP tooling;
     54 +- the OpenAI tunnel client;
     55 +- client credentials and service supervision.
     56 +
     57 +The production target is an onsite Mac Mini. The current POC target is a separate Windo
         ws 11 VM that reproduces machine, credential, restart, loopback, and network separation
         . It does not prove macOS compatibility.
     58 +
     59 +### 3.4 MCPO
     60 +
     61 +**Settled decision**
     62 +
     63 +`MCPO` remains an internal package, repository, image, and compatibility name.
     64 +
     65 +It is poor product naming for the complete system because the repurposed server role is
          not the same thing as the general MCPO service. User-facing documentation should use `
         Vendor Connector` and `Site Connector Container`.
     66 +
     67 +### 3.5 OpenAI tunnel client
     68 +
     69 +**Settled decision**
     70 +
     71 +The OpenAI tunnel client is client-side gateway transport. It is not the vendor server,
          public MCP endpoint, Cloudflare tunnel, or Site Connector Container.
     72 +
     73 +## 4. Machine placement
     74 +
     75 +### 4.1 Main Windows machine
     76 +
     77 +**Settled decision**
     78 +
     79 +The main development machine continues to play these server-side roles:
     80 +
     81 +- BOS Vendor Server;
     82 +- Docker host;
     83 +- development machine;
     84 +- host for the per-site Site Connector Container;
     85 +- host for the current vendor listener and broker experiments.
     86 +
     87 +The Site Connector Container stays on this machine. It is not moved into the client VM.
     88 +
     89 +### 4.2 Server storage
     90 +
     91 +**Context-reported verified**
     92 +
     93 +`S:` is server storage backed by:
     94 +
     95 +`D:\AI-Lighting-Server\AI-Lighting-Server.vhdx`
     96 +
     97 +The fixed deployment root is:
     98 +
     99 +`S:\BOS-Vendor`
    100 +
    101 +The context reports that 16 directories were created and validated:
    102 +
    103 +```text
    104 +S:\BOS-Vendor
    105 +├── releases
    106 +├── compose
    107 +├── config
    108 +├── state
    109 +├── logs
    110 +├── backups
    111 +├── rollback
    112 +└── sites
    113 +    └── 44354
    114 +        ├── config
    115 +        ├── state
    116 +        ├── logs
    117 +        ├── backups
    118 +        ├── rollback
    119 +        └── secrets
    120 +```
    121 +
    122 +The site secrets directory was reported empty. Secret ACL enforcement, backup jobs, res
         tore drills, and rollback jobs are still unimplemented.
    123 +
    124 +### 4.3 Client Gateway VM
    125 +
    126 +**Planned**
    127 +
    128 +The VM contract is:
    129 +
    130 +- name: `BOS-Client-Gateway`;
    131 +- host root: `D:\BOS-Client-Gateway-VM`;
    132 +- guest: Windows 11 x64;
    133 +- memory: 8 GiB;
    134 +- processors: 4;
    135 +- disk: dynamic 80 GiB VDI;
    136 +- firmware: EFI;
    137 +- TPM: 2.0;
    138 +- network: NAT only;
    139 +- inbound NAT forwarding: none;
    140 +- additional network adapters: none;
    141 +- shared folders: disabled;
    142 +- clipboard and clipboard file transfer: disabled;
    143 +- drag and drop: disabled;
    144 +- USB, audio, VRDE, serial, and parallel devices: disabled.
    145 +
    146 +**Context-reported verified**
    147 +
    148 +Oracle VirtualBox `7.2.12r174389` was installed and its CLI was verified. No VM was cre
         ated or started because a verified Windows 11 ISO was not available.
    149 +
    150 +## 5. End-to-end request path
    151 +
    152 +The intended path is:
    153 +
    154 +```text
    155 +ChatGPT or OpenAI MCP client
    156 +        |
    157 +        | public HTTPS and OAuth
    158 +        v
    159 +Cloudflare-published site endpoint
    160 +        |
    161 +        v
    162 +Site Connector Container
    163 +        |
    164 +        | site-pinned vendor MCP request
    165 +        v
    166 +Vendor listener, broker, and tunnel control plane
    167 +        |
    168 +        | existing outbound HTTPS poll and response channel
    169 +        v
    170 +OpenAI tunnel client on the Client Gateway
    171 +        |
    172 +        | loopback MCP with private local authentication
    173 +        v
    174 +127.0.0.1:7087/mcp
    175 +        |
    176 +        v
    177 +Building OS gateway tools and live building state
    178 +```
    179 +
    180 +The response returns through the same chain.
    181 +
    182 +The vendor does not open a new TCP connection into the customer network. The client cre
         ates the outbound HTTPS connection, and the vendor returns work through that establishe
         d channel.
    183 +
    184 +Cloudflare has a separate job. It publishes the server-side vendor listener or public M
         CP endpoint without router port forwarding on the vendor host. It is not the gateway-to
         -vendor tunnel.
    185 +
    186 +## 6. Current POC endpoint contract
    187 +
    188 +### 6.1 Canonical site identity
    189 +
    190 +**Settled decision**
    191 +
    192 +`BOS_VENDOR_SITE_ID` is the canonical site identity and site-facing host port.
    193 +
    194 +Rules:
    195 +
    196 +- exactly five decimal digits;
    197 +- range `10000` through `65535`;
    198 +- current site: `44354`;
    199 +- no separate arbitrary gateway port;
    200 +- the site ID drives the Compose identity, published host port, and site hostnames.
    201 +
    202 +Port `8352` was an arbitrary experiment and was removed from the active contract.
    203 +
    204 +### 6.2 Fixed container port
    205 +
    206 +**Settled decision**
    207 +
    208 +The Site Connector Container listens internally on `8351`.
    209 +
    210 +The site ID does not replace the internal container port. Docker performs the mapping:
    211 +
    212 +`127.0.0.1:44354 -> container 8351`
    213 +
    214 +This keeps the image contract fixed while making the host binding site-specific.
    215 +
    216 +### 6.3 Context-reported port map
    217 +
    218 +| Address or port | Context role | Evidence |
    219 +|---|---|---|
    220 +| `127.0.0.1:44354` | Site Connector Container host binding | Context-reported verified
          |
    221 +| container `8351` | Site Connector internal MCP/OAuth listener | Settled decision |
    222 +| `127.0.0.2:44354` | Separate vendor site listener | Context-reported verified |
    223 +| `127.0.0.1:7087/mcp` | Client Gateway local FastMCP core | Settled client contract |
    224 +| `7085` | Building OS gateway process reported during the POC | Context-reported, fina
         l role unverified |
    225 +| `7088`, `7089` | Supporting Node listeners reported during the POC | Context-reported
         , final role unverified |
    226 +| `8000` | General MCPO admin, OpenAPI, or model surface | Context memory, not accepted
          as final Vendor Connector routing |
    227 +| `8001` | General native MCP surface | Context memory, separate from this site deploym
         ent |
    228 +
    229 +### 6.4 Hostnames
    230 +
    231 +| Hostname | Understood role | State |
    232 +|---|---|---|
    233 +| `44354.ai.lighting` | Existing Cloudflare-published vendor site route used by the POC
          backend | Context-reported working earlier |
    234 +| `44354.gateway.ai.lighting` | Intended public OAuth Site Connector endpoint | DNS and
          public route were reported incomplete |
    235 +| `connect.ai.lighting` | Intended broker or tunnel-control hostname | Current deployme
         nt unknown |
    236 +| `dev.ai.lighting` | General public MCPO test endpoint | Separate from site `44354` |
    237 +
    238 +The POC used a public hairpin from the Site Connector Container to the vendor site rout
         e. The production target is fixed private vendor ingress between server-side components
         .
    239 +
    240 +## 7. Client Gateway runtime
    241 +
    242 +### 7.1 Local MCP boundary
    243 +
    244 +**Settled client contract**
    245 +
    246 +The local tool server is:
    247 +
    248 +`http://127.0.0.1:7087/mcp`
    249 +
    250 +The OpenAI tunnel client dispatches accepted vendor requests to this loopback endpoint.
          The endpoint is not publicly exposed.
    251 +
    252 +### 7.2 Windows client services
    253 +
    254 +**Context-reported verified from earlier work**
    255 +
    256 +The Windows package uses:
    257 +
    258 +- Windows DPAPI with `CurrentUser` scope for stored credentials;
    259 +- Task Scheduler for service supervision;
    260 +- task `BuildingOS-LocalMCP`;
    261 +- task `BuildingOS-HostedTunnel`;
    262 +- an OpenAI-poll runner that points `MCP_SERVER_URL` at `http://127.0.0.1:7087/mcp`.
    263 +
    264 +The existing installer named in context is:
    265 +
    266 +`C:\BuildingOS\host\runtime\server\scripts\install-client-tooling-services.ps1`
    267 +
    268 +The VM must receive the Building OS client package, enroll its vendor credential, insta
         ll the tasks, reboot, and prove recovery after restart.
    269 +
    270 +### 7.3 Client environment values
    271 +
    272 +The context identifies these client settings:
    273 +
    274 +- `BOS_VENDOR_RELAY_URL`;
    275 +- `BOS_VENDOR_SITE_NAME`;
    276 +- `BOS_VENDOR_ENROLLMENT_TOKEN`;
    277 +- `BOS_CHATGPT_MCP_PORT=7087`.
    278 +
    279 +Secret values must not be committed to repository files or copied into documentation.
    280 +
    281 +### 7.4 Mac Mini production port
    282 +
    283 +**Planned and unverified**
    284 +
    285 +The Windows VM is a transport and isolation POC only. The Mac Mini still needs separate
          acceptance for:
    286 +
    287 +- launchd supervision;
    288 +- Keychain credential storage;
    289 +- Apple Silicon executable packaging;
    290 +- firewall behaviour;
    291 +- sleep and wake recovery;
    292 +- installer and upgrade behaviour.
    293 +
    294 +## 8. Vendor-side responsibilities
    295 +
    296 +The Vendor Connector system needs more than the MCPO proxy process.
    297 +
    298 +### 8.1 Site directory and enrollment
    299 +
    300 +The vendor service must map:
    301 +
    302 +- customer;
    303 +- site;
    304 +- gateway;
    305 +- site ID;
    306 +- tunnel identity;
    307 +- active enrollment;
    308 +- allowed tool surface;
    309 +- credentials and revocation state.
    310 +
    311 +### 8.2 Tunnel broker
    312 +
    313 +The broker must provide:
    314 +
    315 +- one active client lease per tunnel;
    316 +- request and response correlation;
    317 +- bounded request and response sizes;
    318 +- deadlines and expiry;
    319 +- durable undispatched work;
    320 +- restart recovery;
    321 +- rate limits;
    322 +- audit events;
    323 +- clear disconnected and degraded states.
    324 +
    325 +An in-memory development broker is not sufficient for production.
    326 +
    327 +### 8.3 Dispatch rules
    328 +
    329 +Before a public request reaches the client gateway, the vendor path must:
    330 +
    331 +- authenticate the public caller;
    332 +- resolve the site and grants;
    333 +- restrict the tool set;
    334 +- remove public authorization, cookie, and proxy headers;
    335 +- enforce payload and deadline limits;
    336 +- inject the private local MCP authentication only at the trusted dispatch boundary.
    337 +
    338 +Pages, HTML, assets, browser cookies, and model traffic must not cross the gateway tool
         ing tunnel.
    339 +
    340 +### 8.4 Failure semantics
    341 +
    342 +The safe recovery rule is:
    343 +
    344 +- undispatched requests may be recovered;
    345 +- dispatched requests with a confirmed response may be completed;
    346 +- dispatched requests without a confirmed result become `unknown`;
    347 +- uncertain mutations must not be replayed automatically.
    348 +
    349 +This prevents a timeout or restart from repeating a building-control write.
    350 +
    351 +## 9. Authority and data ownership
    352 +
    353 +### 9.1 Gateway authority
    354 +
    355 +**Settled decision**
    356 +
    357 +The Building OS gateway remains authoritative for live building state and local tool ex
         ecution.
    358 +
    359 +The vendor may hold identities, grants, tunnel state, audit records, and queued request
         s. It must not silently become the source of truth for the live building.
    360 +
    361 +### 9.2 Site isolation
    362 +
    363 +Each site needs isolated:
    364 +
    365 +- runtime identity;
    366 +- configuration;
    367 +- credentials;
    368 +- state;
    369 +- logs;
    370 +- backups;
    371 +- rollback material;
    372 +- process or container lifecycle.
    373 +
    374 +MCPO JSON state and in-memory runtime connections are useful for a local instance but a
         re not a durable multi-tenant database.
    375 +
    376 +## 10. Authentication and keys
    377 +
    378 +### 10.1 Public MCP OAuth
    379 +
    380 +The public ChatGPT-facing endpoint requires OAuth discovery, registration, authorizatio
         n, consent, token exchange, and protected MCP access.
    381 +
    382 +The POC previously exercised self-hosted OAuth. A consent-page Content Security Policy
         problem blocked HTTP loopback callbacks until loopback callback navigation was allowed.
    383 +
    384 +Production should separate public OAuth from the per-site proxy runtime and give it dur
         able client, grant, token, revocation, and audit storage.
    385 +
    386 +### 10.2 Vendor-to-site authentication
    387 +
    388 +The Site Connector Container uses site-specific authentication when calling the vendor
         site route. Credentials must be scoped to one site and independently revocable.
    389 +
    390 +### 10.3 Model API keys
    391 +
    392 +**Requested design, completed behaviour unknown**
    393 +
    394 +The model or OpenAI-compatible API needs a settings section that can:
    395 +
    396 +- generate a key;
    397 +- show the secret once;
    398 +- store only a verifier or protected secret;
    399 +- attach site, user, scope, and creation metadata;
    400 +- list active keys without revealing secrets;
    401 +- revoke a key immediately;
    402 +- record last use and audit events;
    403 +- keep Codex OAuth-backed credentials separate from issued API keys.
    404 +
    405 +The request to add a Codex OAuth keys section is part of the desired architecture. The
         context does not prove that generation, persistence, authentication, or revocation is c
         urrently built.
    406 +
    407 +### 10.4 Codex OAuth proxy goal
    408 +
    409 +The original model-side goal is to accept an authorized Codex OAuth session behind the
         vendor boundary and expose an OpenAI-compatible endpoint protected by issued API keys.
    410 +
    411 +This belongs to the model/API plane. It is separate from the gateway tool tunnel and mu
         st not be smuggled through the MCP tooling connection.
    412 +
    413 +## 11. General MCPO surfaces
    414 +
    415 +Context memory describes three general MCPO surfaces:
    416 +
    417 +- port `8000` for the admin, OpenAPI, or model-facing application;
    418 +- port `8001` for native MCP;
    419 +- port `8351` for an OAuth-protected public MCP surface.
    420 +
    421 +The remembered parity rule is that native MCP and OAuth MCP expose the same configured
         servers, tools, filtering, calls, code-mode behaviour, and enablement state, while the
         OAuth surface adds discovery, authorization, token enforcement, and public URL handling
         .
    422 +
    423 +These general surfaces must not be confused with the per-site Vendor Connector deployme
         nt or treated as proof of the final production topology.
    424 +
    425 +## 12. Container security
    426 +
    427 +**Context-reported verified for the current POC**
    428 +
    429 +The live Site Connector Container was reported with:
    430 +
    431 +- non-root execution;
    432 +- read-only root filesystem;
    433 +- dropped Linux capabilities;
    434 +- no-new-privileges;
    435 +- PID limit;
    436 +- tmpfs;
    437 +- loopback-only host publication.
    438 +
    439 +The Compose definition later added:
    440 +
    441 +- product and runtime role labels;
    442 +- one CPU limit;
    443 +- `512m` memory limit;
    444 +- `128m` memory reservation.
    445 +
    446 +Those new labels and resource limits were reported inactive because the live container
         was deliberately not recreated.
    447 +
    448 +## 13. Current context-reported state
    449 +
    450 +As last reported:
    451 +
    452 +- container `mcpo-site-44354-gateway-mcpo-1` was running and healthy;
    453 +- image was `mcpo-gateway-poc:local`;
    454 +- host mapping remained `127.0.0.1:44354 -> 8351/tcp`;
    455 +- the existing container did not yet contain the new product label or CPU and memory li
         mits;
    456 +- the container could initialize the vendor backend through `https://44354.ai.lighting/
         mcp`;
    457 +- unauthenticated access to the local Site Connector MCP endpoint returned `401`;
    458 +- `44354.gateway.ai.lighting` DNS and public OAuth routing were incomplete;
    459 +- VirtualBox was installed and Docker remained healthy afterward;
    460 +- no Client Gateway VM or Windows guest existed;
    461 +- `S:\BOS-Vendor` existed with all expected directories;
    462 +- the site secrets directory was empty;
    463 +- focused deployment tests were reported as `27 passed`;
    464 +- the full repository suite was reported as `667 passed, 4 skipped`.
    465 +
    466 +None of these runtime facts was rechecked while creating this document.
    467 +
    468 +## 14. Context-reported build artifacts
    469 +
    470 +The conversation reports these existing artifacts:
    471 +
    472 +- `docs/VENDOR_CONNECTOR_CLIENT_GATEWAY_VM_POC.md`;
    473 +- `docs/OAUTH_TUNNEL_SETUP.md`;
    474 +- `deploy/client-gateway-vm/README.md`;
    475 +- `deploy/client-gateway-vm/New-ClientGatewayVm.ps1`;
    476 +- `deploy/client-gateway-vm/Test-ClientGatewayVm.ps1`;
    477 +- `deploy/client-gateway-vm/client-gateway.env.example`;
    478 +- `deploy/vendor-server/README.md`;
    479 +- `deploy/vendor-server/Initialize-VendorServerStorage.ps1`;
    480 +- `deploy/vendor-server/Test-VendorServerStorage.ps1`;
    481 +- `docker-compose.gateway-poc.yml`;
    482 +- `.claude/diary-2026-07-23.md`.
    483 +
    484 +Their present contents were not inspected for this document.
    485 +
    486 +## 15. Production target
    487 +
    488 +The production system needs:
    489 +
    490 +1. a shared HTTPS edge;
    491 +2. durable external OAuth;
    492 +3. customer, site, gateway, grant, and key storage;
    493 +4. a durable tunnel broker with leases, deadlines, audit, and safe restart semantics;
    494 +5. one isolated Site Connector runtime per site;
    495 +6. fixed private server-side ingress between the Site Connector and broker;
    496 +7. outbound-only client connectors;
    497 +8. explicit tool allowlists and site grants;
    498 +9. independent model/API services;
    499 +10. key generation and revocation;
    500 +11. observability, rate limits, backup, restore, rollback, and high availability.
    501 +
    502 +MCPO is a component source within this system. It is not the whole production service.
    503 +
    504 +## 16. Remaining proof gates
    505 +
    506 +### 16.1 Client VM
    507 +
    508 +- obtain and verify a Windows 11 ISO;
    509 +- create and validate the VM;
    510 +- install Windows;
    511 +- install the Building OS client package;
    512 +- enroll credentials;
    513 +- start the local MCP and tunnel tasks;
    514 +- reboot;
    515 +- prove automatic recovery;
    516 +- prove one safe read through the complete path.
    517 +
    518 +### 16.2 Public site path
    519 +
    520 +- create and verify DNS for the final public Site Connector hostname;
    521 +- route it to the correct loopback-bound container;
    522 +- complete OAuth registration, consent, token exchange, and MCP initialization;
    523 +- prove that one site cannot address another site.
    524 +
    525 +### 16.3 Vendor tunnel
    526 +
    527 +- verify the live `connect.ai.lighting` role and deployment;
    528 +- prove gateway polling, dispatch, response, deadline, disconnect, and restart behaviou
         r;
    529 +- prove that the customer side needs no inbound firewall or port-forwarding rule;
    530 +- prove uncertain mutations are not replayed.
    531 +
    532 +### 16.4 Server operations
    533 +
    534 +- replace the public vendor hairpin with private ingress;
    535 +- enforce secret ACLs;
    536 +- activate Compose labels and resource limits with a controlled recreation;
    537 +- implement backup, restore, and rollback jobs;
    538 +- run recovery drills;
    539 +- add durable audit and operations views.
    540 +
    541 +### 16.5 Mac Mini
    542 +
    543 +- port credential storage to Keychain;
    544 +- port supervision to launchd;
    545 +- package Apple Silicon binaries;
    546 +- test firewall, sleep, wake, restart, upgrade, and recovery.
    547 +
    548 +## 17. Unknowns
    549 +
    550 +The available context does not establish:
    551 +
    552 +- the current live configuration of `connect.ai.lighting`;
    553 +- the current Cloudflare tunnel routing target;
    554 +- a working public `44354.gateway.ai.lighting` OAuth flow;
    555 +- a booted Client Gateway VM;
    556 +- a complete Windows client-to-vendor end-to-end test;
    557 +- Mac Mini parity;
    558 +- production secret ACLs;
    559 +- production backup and rollback;
    560 +- durable production queues, leases, audit, rate limits, or high availability;
    561 +- completed model API key generation and revocation;
    562 +- the final production role of ports `7085`, `7088`, `7089`, or `8000`.
    563 +
    564 +These items require direct inspection or runtime proof before they can be moved from un
         known or planned to verified.
    565 +
    566 +## 18. Decisions that should not drift
    567 +
    568 +- Keep the main machine as the BOS Vendor Server and Docker host.
    569 +- Keep `S:` for server deployment storage.
    570 +- Keep the Client Gateway on a separate machine or VM.
    571 +- Keep the Site Connector Container server-side.
    572 +- Keep the OpenAI tunnel client client-side.
    573 +- Keep the customer connection outbound-only.
    574 +- Keep container port `8351` fixed.
    575 +- Derive the host-facing port from the five-digit site ID.
    576 +- Do not revive arbitrary port `8352`.
    577 +- Do not mix the general tooling MCPO instance with gateway tunnel experiments.
    578 +- Do not call the complete product MCPO.
    579 +- Do not send pages, cookies, assets, or model traffic through the tooling tunnel.
    580 +- Do not replay uncertain building-control writes.
    581 +- Do not present the Windows VM as Mac Mini production proof.