from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = REPO_ROOT / "deploy" / "client-gateway-vm"
CREATE_SCRIPT = ASSET_ROOT / "New-ClientGatewayVm.ps1"
TEST_SCRIPT = ASSET_ROOT / "Test-ClientGatewayVm.ps1"
README = ASSET_ROOT / "README.md"
ENV_EXAMPLE = ASSET_ROOT / "client-gateway.env.example"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_expected_client_gateway_vm_assets_exist() -> None:
    assert CREATE_SCRIPT.is_file()
    assert TEST_SCRIPT.is_file()
    assert README.is_file()
    assert ENV_EXAMPLE.is_file()


def test_creation_script_fixes_the_windows_vm_contract() -> None:
    text = _read(CREATE_SCRIPT)

    for required in (
        'D:\\BOS-Client-Gateway-VM',
        'BOS-Client-Gateway',
        'Windows11_64',
        '8192',
        '81920',
        '[Version]"7.2.0"',
        '"--platform-architecture=x86"',
        '"--memory=8192"',
        '"--cpus=4"',
        '"--firmware=efi"',
        '"--tpm-type=2.0"',
        '"--nic", "1=nat"',
        '"--nat-localhostreachable", "1=off"',
        '"--clipboard-mode=disabled"',
        '"--clipboard-file-transfers=disabled"',
        '"--drag-and-drop=disabled"',
        '"--usb-ohci=off"',
        '"--usb-ehci=off"',
        '"--usb-xhci=off"',
        '"--vrde=off"',
        '"--audio-enabled=off"',
        '"--size=81920"',
        '"--format=VDI"',
        '"--variant=Standard"',
    ):
        assert required in text

    for adapter in range(2, 9):
        assert f'"{adapter}=none"' in text

    assert "Get-PSDrive" in text
    assert '"--platform-arch=x86",' in text
    assert '"ostypes"' in text
    assert "list\", \"vms" in text
    assert "list\", \"hdds" in text
    assert "Test-Path -LiteralPath $WindowsIsoPath -PathType Leaf" in text
    assert "Test-Path -LiteralPath $VmDirectory" in text
    assert "Test-Path -LiteralPath $DiskPath" in text
    assert 'Invoke-VBoxManage @("help", $command)' in text


def test_creation_script_does_not_install_start_download_delete_or_overwrite() -> None:
    text = _read(CREATE_SCRIPT)
    lowered = text.lower()

    for forbidden in (
        "startvm",
        "unregistervm",
        "closemedium",
        "remove-item",
        "clear-content",
        "set-content",
        "out-file",
        "invoke-webrequest",
        "start-bitstransfer",
        "winget",
        "choco",
        "vboxmanage.exe install",
        "--overwrite",
        "--force",
        "nat-pf",
        "--nic1",
        "--boot1",
    ):
        assert forbidden not in lowered


def test_validator_uses_machine_readable_state_and_rejects_isolation_drift() -> None:
    text = _read(TEST_SCRIPT)

    for required in (
        '"showvminfo", $VmName, "--machinereadable"',
        'Assert-Value $values "VMState" "poweroff"',
        'Assert-Value $values "memory" "8192"',
        'Assert-Value $values "cpus" "4"',
        'Assert-Value $values "firmware" "EFI"',
        'Assert-Value $values "tpm-type" "2.0"',
        'Assert-Value $values "nic1" "nat"',
        'Assert-Value $values "clipboard-mode" "disabled"',
        'Assert-Value $values "clipboard-file-transfers" "disabled"',
        'Assert-Value $values "draganddrop" "disabled"',
        'Assert-Value $values "usb" "off"',
        'Assert-Value $values "ehci" "off"',
        'Assert-Value $values "xhci" "off"',
        'Assert-Value $values "vrde" "off"',
        "Forwarding\\(",
        "SharedFolder",
        '"showmediuminfo", "disk", $DiskPath',
    ):
        assert required in text

    for adapter in range(2, 9):
        assert f'Assert-Value $values "nic{adapter}" "none"' in text


def test_readme_keeps_server_connector_and_guest_roles_separate() -> None:
    text = _read(README)

    for required in (
        "Windows 11",
        "Oracle VirtualBox 7.2",
        "D:\\BOS-Client-Gateway-VM",
        "NAT",
        "no port forwarding",
        "install-client-tooling-services.ps1",
        "BuildingOS-LocalMCP",
        "BuildingOS-HostedTunnel",
        "Mac Mini",
        "does not prove macOS parity",
        "New-ClientGatewayVm.ps1",
        "Test-ClientGatewayVm.ps1",
    ):
        assert required in text

    assert "MCPO" in text
    assert "main host" in text
    assert "start the VM manually" in text


def test_environment_example_contains_placeholders_not_credentials() -> None:
    text = _read(ENV_EXAMPLE)

    assert "BOS_VENDOR_RELAY_URL=" in text
    assert "BOS_VENDOR_SITE_NAME=" in text
    assert "BOS_VENDOR_ENROLLMENT_TOKEN=replace-with-one-time-token" in text
    assert "BOS_CHATGPT_MCP_PORT=7087" in text
    assert "BOS_VENDOR_CONNECTOR_KEY" not in text
    assert "BOS_CHATGPT_MCP_API_KEY" not in text
    assert not re.search(r"=(bos_(?:connector|mcp)_[!-~]{12,})$", text, re.MULTILINE)
