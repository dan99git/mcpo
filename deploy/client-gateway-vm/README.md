# Client gateway Windows VM

This creates the powered-off Oracle VirtualBox definition used to test the
Building OS client-gateway boundary in a separate Windows 11 instance.

The main host keeps the BOS server, development checkout, Docker Desktop and
the isolated Site Connector Containers built from the MCPO code lineage. The guest runs only the
Building OS local MCP and outbound hosted-tunnel services.

This proves a separate Windows loopback, filesystem and restart boundary. It
does not prove macOS parity with the onsite Mac Mini. Apple Silicon, launchd,
Keychain, sleep and wake, and the macOS firewall still require a real Mac test.

## Fixed VM contract

- Oracle VirtualBox 7.2 or newer.
- VM name: `BOS-Client-Gateway`.
- Storage root: `D:\BOS-Client-Gateway-VM`.
- Guest OS type: Windows 11 64-bit.
- 8 GiB RAM and 4 virtual CPUs.
- One dynamically allocated 80 GiB VDI.
- EFI firmware and TPM 2.0.
- One NAT adapter only, with no port forwarding.
- No bridged, host-only, internal or second adapter.
- No shared clipboard, clipboard file transfer, drag and drop, shared folder,
  USB controller, USB filter, audio, serial, parallel or VRDE channel.
- The Windows ISO is attached, but the VM is left powered off.

VirtualBox, the Windows ISO and the Building OS checkout are prerequisites.
These scripts do not install VirtualBox, download an ISO, start the VM,
install Windows or place credentials in the VM definition.

## Create the powered-off VM

Supply an existing Windows 11 ISO by absolute path:

```powershell
Set-Location D:\vibe-coded-projects\mcpo

.\deploy\client-gateway-vm\New-ClientGatewayVm.ps1 `
    -WindowsIsoPath 'D:\install-media\Windows11.iso'
```

The script checks the installed VBoxManage version and required 7.2
capabilities, the `Windows11_64` OS type, ISO readability, D: free capacity,
registered VMs, registered disks and every intended path before mutation.

It fails if the VM name, directory or VDI already exists. It never repairs,
unregisters, replaces or deletes a partial VM. If a VirtualBox command fails
after creation begins, inspect and archive the partial assets before retrying.

## Validate isolation

Keep the VM powered off and run:

```powershell
.\deploy\client-gateway-vm\Test-ClientGatewayVm.ps1 `
    -WindowsIsoPath 'D:\install-media\Windows11.iso'
```

The validator reads `VBoxManage showvminfo --machinereadable` and fails on a
wrong resource setting, enabled extra NIC, NAT forwarding rule, shared folder,
USB path, host integration channel, wrong disk, wrong ISO or non-powered-off
state.

Only after that validation passes, start the VM manually in VirtualBox and
install Windows 11. Do not enable VirtualBox shared folders, clipboard, drag
and drop, USB pass-through, bridged networking or NAT port forwarding during
or after installation.

## Install the client tooling inside Windows

Move or clone the Building OS source into the guest without creating a
VirtualBox shared folder. From a PowerShell session inside the guest:

```powershell
Set-Location C:\BuildingOS
.\host\runtime\server\scripts\install-client-tooling-services.ps1
```

That existing installer creates the pinned client runtime, initializes the
local MCP key and registers:

- `BuildingOS-LocalMCP`
- `BuildingOS-HostedTunnel`

Both are current-user Scheduled Tasks triggered at logon. The hosted-tunnel
wrapper waits for authenticated local MCP health before it loads the encrypted
connector credential.

Use `client-gateway.env.example` only as a one-time enrollment reference.
Do not copy a populated version into either repository. The local MCP key and
connector key remain in the guest's existing Windows credential boundaries.

## Acceptance boundary

After Windows and the tooling services are installed:

1. Reboot the guest and confirm both Scheduled Tasks recover without a host
   development shell.
2. Confirm the guest local MCP and routing facade listen on guest loopback
   only.
3. Confirm the guest has outbound vendor connectivity through NAT.
4. Confirm the host still exposes only its loopback Vendor Connector origins.
5. Confirm a safe MCP read reaches this guest through the hosted tunnel.
6. Stop the guest hosted-tunnel task and confirm the remote call fails closed
   instead of reaching a host-local fallback.
7. Start the task and confirm a later safe read recovers without replaying an
   uncertain write.

Run `Test-ClientGatewayVm.ps1` again with the VM powered off after any
VirtualBox settings change.
