"""Read-only system information collection helpers.

All functions in this module gather information using normal OS APIs,
read-only registry access, or read-only shell queries. Nothing here writes to
the system, changes identifiers, or requires administrator privileges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

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
        InfoItem("User", "Username", _username()),
        InfoItem("User", "Computer name", _computer_name()),
        InfoItem("Operating System", "OS version/build", _os_version()),
        InfoItem("Hardware", "CPU name", _cpu_name()),
        InfoItem("Hardware", "GPU name", _gpu_name()),
        InfoItem("Hardware", "RAM amount", _ram_amount()),
        InfoItem("Hardware", "Board/system manufacturer", _baseboard_value("Manufacturer")),
        InfoItem("Hardware", "Board/system product", _baseboard_value("Product")),
        InfoItem("Hardware", "Board/system serial", _baseboard_value("SerialNumber")),
        InfoItem("Firmware", "Firmware/BIOS serial", _bios_value("SerialNumber")),
        InfoItem("Firmware", "Firmware/BIOS version", _bios_value("SMBIOSBIOSVersion")),
        InfoItem("Storage", "Disk model/serial", _disk_models_and_serials()),
        InfoItem("Network", "MAC addresses", _mac_addresses()),
        InfoItem("Network", "Local IP address", _local_ip()),
        InfoItem("Network", "Public IP address", _public_ip()),
        InfoItem("Identifiers", "Machine identifier", _machine_identifier()),
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
        if text and text != UNAVAILABLE:
            return text
    return UNAVAILABLE


def _username() -> str:
    return _first_value(os.environ.get("USERNAME"), os.environ.get("USER"), _safe_get(os.getlogin), _safe_get(getpass.getuser))


def _computer_name() -> str:
    return _first_value(os.environ.get("COMPUTERNAME"), platform.node(), socket.gethostname())


def _os_version() -> str:
    system = platform.system()
    if system != "Windows":
        return _normalize(platform.platform())

    caption = _powershell_cim_value("Win32_OperatingSystem", "Caption")
    version = _powershell_cim_value("Win32_OperatingSystem", "Version")
    build = _powershell_cim_value("Win32_OperatingSystem", "BuildNumber")
    parts = [part for part in [caption, f"Version {version}" if version != UNAVAILABLE else "", f"Build {build}" if build != UNAVAILABLE else ""] if part]
    return " - ".join(parts) if parts else _normalize(platform.platform())


def _cpu_name() -> str:
    system = platform.system()
    if system == "Darwin":
        value = _command_output(["sysctl", "-n", "machdep.cpu.brand_string"])
        if value != UNAVAILABLE:
            return value
    elif system == "Linux":
        value = _linux_cpu_name()
        if value != UNAVAILABLE:
            return value

    value = _powershell_cim_value("Win32_Processor", "Name")
    if value != UNAVAILABLE:
        return value
    return _normalize(platform.processor())


def _gpu_name() -> str:
    system = platform.system()
    if system == "Darwin":
        value = _system_profiler_values("SPDisplaysDataType", "Chipset Model")
        if value != UNAVAILABLE:
            return value
    elif system == "Linux":
        value = _linux_gpu_name()
        if value != UNAVAILABLE:
            return value

    return _powershell_cim_value("Win32_VideoController", "Name", multiple=True)


def _ram_amount() -> str:
    if psutil is not None:
        try:
            total = psutil.virtual_memory().total
            return _format_bytes_as_gb(total)
        except Exception:
            pass

    system = platform.system()
    if system == "Darwin":
        value = _command_output(["sysctl", "-n", "hw.memsize"])
        if value != UNAVAILABLE and value.isdigit():
            return _format_bytes_as_gb(int(value))
    elif system == "Linux":
        value = _linux_mem_total()
        if value != UNAVAILABLE:
            return value

    total_gb = _powershell_command(
        "[Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)"
    )
    return f"{total_gb} GB" if total_gb != UNAVAILABLE else UNAVAILABLE


def _baseboard_value(property_name: str) -> str:
    system = platform.system()
    if system == "Darwin":
        mac_values = {
            "Manufacturer": "Apple Inc.",
            "Product": _command_output(["sysctl", "-n", "hw.model"]),
            "SerialNumber": _mac_hardware_value("Serial Number"),
        }
        return _normalize(mac_values.get(property_name))

    if system == "Linux":
        linux_files = {
            "Manufacturer": "/sys/class/dmi/id/board_vendor",
            "Product": "/sys/class/dmi/id/board_name",
            "SerialNumber": "/sys/class/dmi/id/board_serial",
        }
        return _read_first_line(linux_files.get(property_name, ""))

    return _powershell_cim_value("Win32_BaseBoard", property_name)


def _bios_value(property_name: str) -> str:
    system = platform.system()
    if system == "Darwin":
        mac_values = {
            "SerialNumber": _mac_hardware_value("Serial Number"),
            "SMBIOSBIOSVersion": _mac_hardware_value("System Firmware Version"),
        }
        return _normalize(mac_values.get(property_name))

    if system == "Linux":
        linux_files = {
            "SerialNumber": "/sys/class/dmi/id/product_serial",
            "SMBIOSBIOSVersion": "/sys/class/dmi/id/bios_version",
        }
        return _read_first_line(linux_files.get(property_name, ""))

    return _powershell_cim_value("Win32_BIOS", property_name)


def _disk_models_and_serials() -> str:
    system = platform.system()
    if system == "Darwin":
        value = _mac_disk_models_and_serials()
        if value != UNAVAILABLE:
            return value
    elif system == "Linux":
        value = _linux_disk_models_and_serials()
        if value != UNAVAILABLE:
            return value

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


def _machine_identifier() -> str:
    system = platform.system()
    if system == "Windows" and winreg is not None:
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

    if system == "Darwin":
        value = _mac_hardware_value("Hardware UUID")
        if value != UNAVAILABLE:
            return value

    if system == "Linux":
        value = _first_value(
            _read_first_line("/etc/machine-id"),
            _read_first_line("/var/lib/dbus/machine-id"),
        )
        if value != UNAVAILABLE:
            return value

    return UNAVAILABLE


def _linux_cpu_name() -> str:
    try:
        with Path("/proc/cpuinfo").open(encoding="utf-8", errors="replace") as cpuinfo:
            for line in cpuinfo:
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() in {"model name", "hardware", "processor"}:
                    return _normalize(value)
    except OSError:
        pass
    return UNAVAILABLE


def _linux_mem_total() -> str:
    try:
        with Path("/proc/meminfo").open(encoding="utf-8", errors="replace") as meminfo:
            for line in meminfo:
                key, separator, value = line.partition(":")
                if separator and key == "MemTotal":
                    amount, _, unit = value.strip().partition(" ")
                    if amount.isdigit() and unit.lower().startswith("kb"):
                        return _format_bytes_as_gb(int(amount) * 1024)
    except OSError:
        pass
    return UNAVAILABLE


def _linux_gpu_name() -> str:
    if shutil.which("lspci"):
        output = _command_output(["lspci"])
        lines = [
            line
            for line in output.splitlines()
            if any(marker in line.lower() for marker in ("vga compatible controller", "3d controller", "display controller"))
        ]
        if lines:
            return "; ".join(line.split(":", 2)[-1].strip() for line in lines)

    drm_names: list[str] = []
    for vendor_file in Path("/sys/class/drm").glob("card*/device/vendor"):
        device_file = vendor_file.with_name("device")
        vendor = _read_first_line(str(vendor_file))
        device = _read_first_line(str(device_file))
        if vendor != UNAVAILABLE and device != UNAVAILABLE:
            drm_names.append(f"PCI vendor {vendor}, device {device}")
    return "; ".join(dict.fromkeys(drm_names)) if drm_names else UNAVAILABLE


def _linux_disk_models_and_serials() -> str:
    if shutil.which("lsblk"):
        output = _command_output(["lsblk", "-d", "-n", "-o", "MODEL,SERIAL"])
        if output != UNAVAILABLE:
            values = [" ".join(line.split()) for line in output.splitlines() if line.strip()]
            if values:
                return "; ".join(values)
    return UNAVAILABLE


def _mac_disk_models_and_serials() -> str:
    names = _system_profiler_values("SPStorageDataType", "Device Name")
    media_names = _system_profiler_values("SPNVMeDataType", "Model")
    return _first_value(names, media_names)


def _mac_hardware_value(label: str) -> str:
    if label == "Serial Number":
        return _first_value(
            _system_profiler_values("SPHardwareDataType", "Serial Number"),
            _system_profiler_values("SPHardwareDataType", "Serial Number (system)"),
        )
    return _system_profiler_values("SPHardwareDataType", label)


@lru_cache(maxsize=8)
def _system_profiler_output(data_type: str) -> str:
    output = _command_output(["system_profiler", data_type])
    if output == UNAVAILABLE:
        return UNAVAILABLE
    return output


def _system_profiler_values(data_type: str, label: str) -> str:
    output = _system_profiler_output(data_type)
    if output == UNAVAILABLE:
        return UNAVAILABLE

    prefix = f"{label}:"
    values = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            values.append(stripped.removeprefix(prefix).strip())
    return "; ".join(dict.fromkeys(value for value in values if value)) or UNAVAILABLE


def _read_first_line(path: str) -> str:
    if not path:
        return UNAVAILABLE
    try:
        return _normalize(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[0])
    except (OSError, IndexError):
        return UNAVAILABLE


def _command_output(command: list[str], timeout: int = 8) -> str:
    if not command or shutil.which(command[0]) is None:
        return UNAVAILABLE

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return UNAVAILABLE

    if completed.returncode != 0:
        return UNAVAILABLE
    return _normalize(completed.stdout)


def _format_bytes_as_gb(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GB"


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
