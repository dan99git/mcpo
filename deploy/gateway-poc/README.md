# Vendor Connector proof of concept

This is the server-side Building OS Vendor Connector proof of concept. Each
deployment is one Site Connector Container built from the MCPO codebase. It
keeps the live MCPO-derived project, service, image, volume and environment
identifiers so the existing deployment is not migrated by a naming change.

This container is not the onsite gateway tunnel client. The onsite Windows
gateway VM, and later the Mac mini, runs the gateway runtime, local MCP tool
server and outbound tunnel client. This container remains on the main Vendor
Server and relays authenticated MCP traffic to that site through the configured
vendor route.

It does not use the general tooling MCPO instance on port `8351` or that
instance's OAuth state.

## Local endpoint

- OAuth and MCP: `http://127.0.0.1:<BOS_VENDOR_SITE_ID>`
- Public OAuth base: `https://<BOS_VENDOR_SITE_ID>.<MCPO_GATEWAY_PUBLIC_DOMAIN>`
- Building OS backend: `https://<BOS_VENDOR_SITE_ID>.<VENDOR_PUBLIC_DOMAIN>/mcp`

The backend route is a deliberate public hairpin for the proof of concept.
Replace it with the private vendor ingress before production use. The container
does not establish the onsite tunnel itself.

`BOS_VENDOR_SITE_ID` is the single site and host-port identity. It must be exactly
five decimal digits in the range `10000..65535`. Compose publishes that host port
to MCPO's fixed container port `8351`; no second gateway port setting exists.

## Start

```powershell
Copy-Item deploy/gateway-poc/env.example .env.gateway-poc
# Set the site/domain values and replace both secret placeholders.

docker compose --env-file .env.gateway-poc -f docker-compose.gateway-poc.yml config --quiet
docker compose --env-file .env.gateway-poc -f docker-compose.gateway-poc.yml up -d --build
```

The Compose project name, container network, named data volume, configuration,
credentials, OAuth state, process and host port are separate from the default stack.
The preserved live identifiers are:

- project: `mcpo-site-<BOS_VENDOR_SITE_ID>`
- service: `gateway-mcpo`
- image: `mcpo-gateway-poc:local`
- volume: `gateway_poc_data`

Runtime labels identify the product role as `vendor-connector`, the runtime role
as `site-connector-container`, the MCPO lineage and the site ID. The Compose
definition now declares one CPU, a `512m` memory limit and a `128m` memory
reservation. These bounds are not active on the already running container. They
become effective only after a controlled recreation and runtime reinspection.

## Check

```powershell
$siteId = (Select-String '^BOS_VENDOR_SITE_ID=' .env.gateway-poc).Line.Split('=', 2)[1]
Invoke-RestMethod "http://127.0.0.1:$siteId/.well-known/oauth-authorization-server"
docker compose --env-file .env.gateway-poc -f docker-compose.gateway-poc.yml ps
```

The public Cloudflare hostname must route only
`<BOS_VENDOR_SITE_ID>.<MCPO_GATEWAY_PUBLIC_DOMAIN>` to
`http://localhost:<BOS_VENDOR_SITE_ID>`. The existing
`dev.ai.lighting` to `http://localhost:8351` route remains unchanged.

After the public route is active, register
`https://<BOS_VENDOR_SITE_ID>.<MCPO_GATEWAY_PUBLIC_DOMAIN>/mcp` as a separate
OAuth MCP server.
