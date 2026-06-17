"""Read-only system information collection helpers.

All functions in this module gather information using normal OS APIs,
read-only registry access, or read-only shell queries. Nothing here writes to
the system, changes identifiers, or requires administrator privileges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import getpass
import json
import os
import platform
import socket
import subprocess
import urllib.request

try:
    import psutil
except ImportError:  # pragma: no cover - handled gracefully at runtime
    psutil = None

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows development machines
    winreg = None


UNAVAILABLE = "Unavailable"


@dataclass(frozen=True)
class InfoItem:
    """A single display row in the report."""

    category: str
    label: str
    value: str


def collect_system_info() -> list[InfoItem]:
    """Collect all information shown by the app."""

    started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    info: list[InfoItem] = [
        InfoItem("User", "Windows username", _username()),
        InfoItem("User", "Computer name", _first_value(os.environ.get("COMPUTERNAME"), platform.node())),
        InfoItem("Windows", "Windows version/build", _windows_version()),
        InfoItem("Hardware", "CPU name", _cpu_name()),
        InfoItem("Hardware", "GPU name", _gpu_name()),
        InfoItem("Hardware", "RAM amount", _ram_amount()),
        InfoItem("Hardware", "Motherboard manufacturer", _baseboard_value("Manufacturer")),
        InfoItem("Hardware", "Motherboard product", _baseboard_value("Product")),
        InfoItem("Hardware", "Motherboard serial", _baseboard_value("SerialNumber")),
        InfoItem("Firmware", "BIOS serial", _bios_value("SerialNumber")),
        InfoItem("Firmware", "BIOS version", _bios_value("SMBIOSBIOSVersion")),
        InfoItem("Storage", "Disk model/serial", _disk_models_and_serials()),
        InfoItem("Network", "MAC addresses", _mac_addresses()),
        InfoItem("Network", "Local IP address", _local_ip()),
        InfoItem("Network", "Public IP address", _public_ip()),
        InfoItem("Identifiers", "Machine GUID / HWID-style identifier", _machine_guid()),
        InfoItem("Runtime", "Python version", platform.python_version()),
        InfoItem("Runtime", "App run time/date", started_at),
    ]
    return info


def format_report(items: list[InfoItem]) -> str:
    """Render collected items as a plain-text report."""

    lines = ["System Info Checker Report", "=" * 26, ""]
    current_category = ""

    for item in items:
        if item.category != current_category:
            if current_category:
                lines.append("")
            current_category = item.category
            lines.append(f"[{current_category}]")
        lines.append(f"{item.label}: {item.value}")

    lines.append("")
    lines.append("Note: This report is read-only and does not modify system identifiers.")
    return "\n".join(lines)


def _safe_get(func, default: str = UNAVAILABLE) -> str:
    try:
        value = func()
    except Exception:
        return default
    return _normalize(value, default)


def _normalize(value, default: str = UNAVAILABLE) -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    text = str(value).strip()
    return text if text else default


def _first_value(*values) -> str:
    for value in values:
        text = _normalize(value, "")
        if text:
            return text
    return UNAVAILABLE


def _username() -> str:
    return _first_value(os.environ.get("USERNAME"), os.environ.get("USER"), _safe_get(os.getlogin), _safe_get(getpass.getuser))


def _windows_version() -> str:
    if platform.system() != "Windows":
        return _normalize(platform.platform())

    caption = _powershell_cim_value("Win32_OperatingSystem", "Caption")
    version = _powershell_cim_value("Win32_OperatingSystem", "Version")
    build = _powershell_cim_value("Win32_OperatingSystem", "BuildNumber")
    parts = [part for part in [caption, f"Version {version}" if version != UNAVAILABLE else "", f"Build {build}" if build != UNAVAILABLE else ""] if part]
    return " - ".join(parts) if parts else _normalize(platform.platform())


def _cpu_name() -> str:
    value = _powershell_cim_value("Win32_Processor", "Name")
    if value != UNAVAILABLE:
        return value
    return _normalize(platform.processor())


def _gpu_name() -> str:
    return _powershell_cim_value("Win32_VideoController", "Name", multiple=True)


def _ram_amount() -> str:
    if psutil is not None:
        try:
            total = psutil.virtual_memory().total
            return f"{total / (1024 ** 3):.2f} GB"
        except Exception:
            pass

    total_kb = _powershell_command(
        "[Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)"
    )
    return f"{total_kb} GB" if total_kb != UNAVAILABLE else UNAVAILABLE


def _baseboard_value(property_name: str) -> str:
    return _powershell_cim_value("Win32_BaseBoard", property_name)


def _bios_value(property_name: str) -> str:
    return _powershell_cim_value("Win32_BIOS", property_name)


def _disk_models_and_serials() -> str:
    ps_value = _powershell_command(
        "Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,SerialNumber | ConvertTo-Json -Compress"
    )
    rows = _parse_json_rows(ps_value)
    formatted: list[str] = []

    for row in rows:
        model = _normalize(row.get("Model"), "")
        serial = _normalize(row.get("SerialNumber"), "")
        if model and serial:
            formatted.append(f"{model} ({serial})")
        elif model:
            formatted.append(model)
        elif serial:
            formatted.append(serial)

    return "; ".join(formatted) if formatted else UNAVAILABLE


def _mac_addresses() -> str:
    addresses: list[str] = []

    if psutil is not None:
        try:
            for adapter_name, adapter_addresses in psutil.net_if_addrs().items():
                for address in adapter_addresses:
                    if _looks_like_mac(address.address):
                        addresses.append(f"{adapter_name}: {address.address}")
        except Exception:
            pass

    if not addresses:
        ps_value = _powershell_command(
            "Get-CimInstance Win32_NetworkAdapterConfiguration | "
            "Where-Object {$_.MACAddress} | Select-Object Description,MACAddress | ConvertTo-Json -Compress"
        )
        rows = _parse_json_rows(ps_value)
        for row in rows:
            description = _normalize(row.get("Description"), "Adapter")
            mac = _normalize(row.get("MACAddress"), "")
            if mac:
                addresses.append(f"{description}: {mac}")

    return "\n".join(dict.fromkeys(addresses)) if addresses else UNAVAILABLE


def _looks_like_mac(value: str) -> bool:
    if not value:
        return False
    separators = value.count(":") + value.count("-")
    compact = value.replace(":", "").replace("-", "")
    return separators >= 5 and len(compact) == 12


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return _normalize(sock.getsockname()[0])
    except Exception:
        return _safe_get(lambda: socket.gethostbyname(socket.gethostname()), UNAVAILABLE)


def _public_ip() -> str:
    try:
        request = urllib.request.Request(
            "https://api.ipify.org",
            headers={"User-Agent": "SystemInfoChecker/1.0"},
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            return _normalize(response.read())
    except Exception:
        return UNAVAILABLE


def _machine_guid() -> str:
    if platform.system() == "Windows" and winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return _normalize(value)
        except Exception:
            pass

    return UNAVAILABLE


def _powershell_cim_value(class_name: str, property_name: str, multiple: bool = False) -> str:
    command = (
        f"Get-CimInstance {class_name} | "
        f"Select-Object -ExpandProperty {property_name} | "
        "Where-Object {$_} | Select-Object -Unique"
    )
    value = _powershell_command(command)
    if value == UNAVAILABLE:
        value = _wmic_value(class_name, property_name)
    if multiple and value != UNAVAILABLE:
        return "; ".join(line.strip() for line in value.splitlines() if line.strip())
    return value


def _powershell_command(command: str) -> str:
    if platform.system() != "Windows":
        return UNAVAILABLE

    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return UNAVAILABLE

    if completed.returncode != 0:
        return UNAVAILABLE
    return _normalize(completed.stdout)


def _wmic_value(class_name: str, property_name: str) -> str:
    if platform.system() != "Windows":
        return UNAVAILABLE

    wmic_class = class_name.replace("Win32_", "")
    try:
        completed = subprocess.run(
            ["wmic", wmic_class, "get", property_name, "/value"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return UNAVAILABLE

    if completed.returncode != 0:
        return UNAVAILABLE

    values = []
    for line in completed.stdout.splitlines():
        prefix = f"{property_name}="
        if line.startswith(prefix):
            values.append(line.removeprefix(prefix).strip())
    return "\n".join(value for value in values if value) or UNAVAILABLE


def _parse_json_rows(value: str) -> list[dict]:
    if value == UNAVAILABLE:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []
