# Building OS Vendor Server

The Vendor Server stays on the main Windows development machine. It hosts the
Building OS server code and one isolated Vendor Connector deployment for each
site.

Each Vendor Connector runs as a Site Connector Container built from the MCPO
codebase. It terminates the public OAuth MCP connection and relays authenticated
requests to the configured site route. It is not the onsite gateway tunnel
client.

The onsite Windows gateway VM, and later the Mac mini, runs the gateway runtime,
the local MCP tool server and the outbound tunnel client. The Vendor Server does
not move those client-side components into this repository or storage root.

## Storage root

The canonical Vendor Server data root is:

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

The storage scripts only accept a fully qualified root below drive `S:`. They
reject another drive, a relative path, the drive root itself and an existing
reparse-point path. `SiteId` must be exactly five decimal digits in the range
`10000..65535` and defaults to `44354`.

The initializer creates directories only. It does not create secret values,
secret files, environment files, credentials, OAuth tokens or site access keys.
The per-site `secrets` directory is intentionally empty. Secret ACL enforcement
and the backup policy remain unimplemented and must be completed before storing
production credentials or treating backups as operational.

## Preview

This reports the directories that would be created and makes no changes:

```powershell
.\deploy\vendor-server\Initialize-VendorServerStorage.ps1 -SiteId 44354 -WhatIf
```

## Initialize

Run this only when the `S:` storage change is authorized:

```powershell
.\deploy\vendor-server\Initialize-VendorServerStorage.ps1 -SiteId 44354 -Confirm
```

The initializer is idempotent. Existing valid directories are left in place.

## Validate

The validator is read-only. It exits with an error if the root or any required
root or per-site directory is missing, is not a directory, is a reparse point or
does not belong to the `S:` PowerShell drive.

```powershell
.\deploy\vendor-server\Test-VendorServerStorage.ps1 -SiteId 44354
```

## Live connector compatibility

The current proof of concept remains in
`docker-compose.gateway-poc.yml` and `deploy\gateway-poc`. Its project, service,
image, volume, ports, environment variables and runtime command remain unchanged.
The product-role labels distinguish the Vendor Connector and Site Connector
Container from the onsite gateway tunnel client.
