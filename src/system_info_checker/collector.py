"""Read-only system information collection helpers.

All functions in this module gather information using normal OS APIs,
read-only registry access, or read-only shell queries. Nothing here writes to
the system, changes identifiers, or requires administrator privileges.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
import time
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
HIDDEN_BY_OS = "Hidden by macOS privacy"
COLLECTION_WORKERS = 8
PUBLIC_IP_TIMEOUT_SECONDS = 1


@dataclass(frozen=True)
class InfoItem:
    """A single display row in the report."""

    category: str
    label: str
    value: str
    sensitive: bool = False


def collect_system_info() -> list[InfoItem]:
    """Collect all information shown by the app."""

    started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if platform.system() == "Windows":
        _windows_cim_snapshot.cache_clear()
        _windows_cim_snapshot()

    item_specs: list[tuple[str, str, Callable[[], str], bool]] = [
        ("User", "Username", _username, True),
        ("User", "Computer name", _computer_name, True),
        ("Operating System", "OS version/build", _os_version, False),
        ("Hardware", "CPU name", _cpu_name, False),
        ("Hardware", "GPU name", _gpu_name, False),
        ("Hardware", "RAM amount", _ram_amount, False),
        ("Hardware", "Board/system manufacturer", lambda: _baseboard_value("Manufacturer"), False),
        ("Hardware", "Board/system product", lambda: _baseboard_value("Product"), False),
        ("Hardware", "Board/system serial", lambda: _baseboard_value("SerialNumber"), True),
        ("Firmware", "Firmware/BIOS serial", lambda: _bios_value("SerialNumber"), True),
        ("Firmware", "Firmware/BIOS version", lambda: _bios_value("SMBIOSBIOSVersion"), False),
        ("Health", "CPU usage", _cpu_usage, False),
        ("Health", "Memory usage", _memory_usage, False),
        ("Health", "System volume free space", _system_volume_space, False),
        ("Health", "Boot time / uptime", _boot_time_and_uptime, False),
        ("Health", "Battery status", _battery_status, False),
        ("Health", "Battery health", _battery_health, False),
        ("Storage", "Disk model/serial", _disk_models_and_serials, True),
        ("Storage", "Storage capacity/free space", _storage_capacity_free_space, False),
        ("Network", "MAC addresses", _mac_addresses, True),
        ("Network", "Local IP address", _local_ip, True),
        ("Network", "Public IP address", _public_ip, True),
        ("Network", "Active network adapter", _active_network_adapter, True),
        ("Network", "Wi-Fi SSID", _wifi_ssid, True),
        ("Device Details", "Display resolution/refresh rate", _display_resolution_refresh_rate, False),
        ("Device Details", "System architecture", _system_architecture, False),
        ("Device Details", "Virtualization status", _virtualization_status, False),
        ("Security", "Secure Boot status", _secure_boot_status, False),
        ("Security", "TPM status", _tpm_status, False),
        ("Identifiers", "Machine identifier", _machine_identifier, True),
        ("Runtime", "Python version", platform.python_version, False),
        ("Runtime", "App run time/date", lambda: started_at, False),
    ]
    values = _collect_values(item_specs)
    return [
        InfoItem(category, label, value, sensitive)
        for (category, label, _, sensitive), value in zip(item_specs, values)
    ]


def _collect_values(item_specs: list[tuple[str, str, Callable[[], str], bool]]) -> list[str]:
    worker_count = min(COLLECTION_WORKERS, len(item_specs))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_collect_value, getter) for _, _, getter, _ in item_specs]
        return [future.result() for future in futures]


def _collect_value(getter: Callable[[], str]) -> str:
    try:
        return _normalize(getter())
    except Exception:
        return UNAVAILABLE


def format_report(items: list[InfoItem], redact_sensitive: bool = False) -> str:
    """Render collected items as a plain-text report."""

    lines = ["System Info Checker Report", "=" * 26, ""]
    current_category = ""

    for item in items:
        if item.category != current_category:
            if current_category:
                lines.append("")
            current_category = item.category
            lines.append(f"[{current_category}]")
        lines.append(f"{item.label}: {item_value(item, redact_sensitive)}")

    lines.append("")
    lines.append("Note: This report is read-only and does not modify system identifiers.")
    return "\n".join(lines)


def format_json_report(items: list[InfoItem], redact_sensitive: bool = False) -> str:
    """Render collected items as a JSON snapshot for export or comparison."""

    payload = {
        "app": "System Info Checker",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "redacted": redact_sensitive,
        "items": [
            {
                "category": item.category,
                "label": item.label,
                "value": item_value(item, redact_sensitive),
                "sensitive": item.sensitive,
            }
            for item in items
        ],
    }
    return json.dumps(payload, indent=2)


def item_value(item: InfoItem, redact_sensitive: bool = False) -> str:
    """Return a row value, masking sensitive information when requested."""

    if not redact_sensitive or not item.sensitive or item.value in {UNAVAILABLE, HIDDEN_BY_OS}:
        return item.value
    return _redact_value(item.value)


def _redact_value(value: str) -> str:
    if "\n" not in value:
        return "Redacted"

    redacted_lines = []
    for line in value.splitlines():
        label, separator, _ = line.partition(":")
        redacted_lines.append(f"{label}{separator} Redacted" if separator else "Redacted")
    return "\n".join(redacted_lines)


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


@lru_cache(maxsize=1)
def _windows_cim_snapshot() -> dict:
    if platform.system() != "Windows":
        return {}

    command = (
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$gpu = @(Get-CimInstance Win32_VideoController | "
        "Select-Object -ExpandProperty Name | Where-Object {$_} | Select-Object -Unique); "
        "$base = Get-CimInstance Win32_BaseBoard | Select-Object -First 1; "
        "$bios = Get-CimInstance Win32_BIOS | Select-Object -First 1; "
        "$disks = @(Get-CimInstance Win32_DiskDrive | ForEach-Object { "
        "[ordered]@{ Model = $_.Model; SerialNumber = $_.SerialNumber } }); "
        "[ordered]@{ "
        "OperatingSystem = [ordered]@{ Caption = $os.Caption; Version = $os.Version; BuildNumber = $os.BuildNumber }; "
        "CPUName = $cpu.Name; "
        "GPUNames = $gpu; "
        "BaseBoard = [ordered]@{ Manufacturer = $base.Manufacturer; Product = $base.Product; SerialNumber = $base.SerialNumber }; "
        "BIOS = [ordered]@{ SerialNumber = $bios.SerialNumber; SMBIOSBIOSVersion = $bios.SMBIOSBIOSVersion }; "
        "Disks = $disks "
        "} | ConvertTo-Json -Compress -Depth 4"
    )
    value = _powershell_command(command, timeout=6)
    if value == UNAVAILABLE:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _windows_cim_snapshot_section(section_name: str) -> dict:
    section = _windows_cim_snapshot().get(section_name)
    return section if isinstance(section, dict) else {}


def _format_disk_rows(rows: list[dict]) -> str:
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


def _username() -> str:
    return _first_value(os.environ.get("USERNAME"), os.environ.get("USER"), _safe_get(os.getlogin), _safe_get(getpass.getuser))


def _computer_name() -> str:
    return _first_value(os.environ.get("COMPUTERNAME"), platform.node(), socket.gethostname())


def _os_version() -> str:
    system = platform.system()
    if system != "Windows":
        return _normalize(platform.platform())

    os_info = _windows_cim_snapshot_section("OperatingSystem")
    if os_info:
        caption = _normalize(os_info.get("Caption"), "")
        version = _normalize(os_info.get("Version"), "")
        build = _normalize(os_info.get("BuildNumber"), "")
    else:
        caption = _powershell_cim_value("Win32_OperatingSystem", "Caption")
        version = _powershell_cim_value("Win32_OperatingSystem", "Version")
        build = _powershell_cim_value("Win32_OperatingSystem", "BuildNumber")
    parts = [
        part
        for part in [
            caption if caption != UNAVAILABLE else "",
            f"Version {version}" if version and version != UNAVAILABLE else "",
            f"Build {build}" if build and build != UNAVAILABLE else "",
        ]
        if part
    ]
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

    if system == "Windows":
        value = _normalize(_windows_cim_snapshot().get("CPUName"), "")
        if value:
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

    if system == "Windows":
        names = _windows_cim_snapshot().get("GPUNames")
        if isinstance(names, list):
            value = "; ".join(_normalize(name, "") for name in names if _normalize(name, ""))
            if value:
                return value
        value = _normalize(names, "")
        if value:
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


def _cpu_usage() -> str:
    if psutil is None:
        return UNAVAILABLE
    try:
        return f"{psutil.cpu_percent(interval=0.2):.1f}%"
    except Exception:
        return UNAVAILABLE


def _memory_usage() -> str:
    if psutil is None:
        return UNAVAILABLE
    try:
        memory = psutil.virtual_memory()
    except Exception:
        return UNAVAILABLE
    return f"{_format_bytes_as_gb(memory.used)} used / {_format_bytes_as_gb(memory.total)} total ({memory.percent:.1f}%)"


def _system_volume_space() -> str:
    if psutil is None:
        return UNAVAILABLE
    try:
        usage = psutil.disk_usage(_system_volume_path())
    except Exception:
        return UNAVAILABLE
    return f"{_format_bytes_as_gb(usage.free)} free / {_format_bytes_as_gb(usage.total)} total ({usage.percent:.1f}% used)"


def _storage_capacity_free_space() -> str:
    if psutil is None:
        return UNAVAILABLE

    rows: list[str] = []
    seen_mounts: set[str] = set()
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        partitions = []

    for partition in partitions:
        mountpoint = _normalize(getattr(partition, "mountpoint", ""), "")
        if not mountpoint or mountpoint in seen_mounts:
            continue
        try:
            usage = psutil.disk_usage(mountpoint)
        except Exception:
            continue
        seen_mounts.add(mountpoint)
        rows.append(f"{mountpoint}: {_format_bytes_as_gb(usage.free)} free / {_format_bytes_as_gb(usage.total)} total")

    if rows:
        return "\n".join(rows)
    return _system_volume_space()


def _boot_time_and_uptime() -> str:
    if psutil is None:
        return UNAVAILABLE
    try:
        boot_time = datetime.fromtimestamp(psutil.boot_time()).astimezone()
        uptime_seconds = max(0, int(time.time() - psutil.boot_time()))
    except Exception:
        return UNAVAILABLE
    return f"{boot_time.strftime('%Y-%m-%d %H:%M:%S %Z')} ({_format_duration(uptime_seconds)} uptime)"


def _battery_status() -> str:
    if psutil is None or not hasattr(psutil, "sensors_battery"):
        return UNAVAILABLE
    try:
        battery = psutil.sensors_battery()
    except Exception:
        return UNAVAILABLE
    if battery is None:
        return "No battery detected"

    state = "plugged in" if battery.power_plugged else "on battery"
    remaining = ""
    if battery.secsleft not in (None, psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        remaining = f", {_format_duration(int(battery.secsleft))} remaining"
    return f"{battery.percent:.0f}% - {state}{remaining}"


def _battery_health() -> str:
    system = platform.system()
    if system == "Darwin":
        parts = [
            _prefixed_value("Condition", _system_profiler_values("SPPowerDataType", "Condition")),
            _prefixed_value("Cycle count", _system_profiler_values("SPPowerDataType", "Cycle Count")),
            _prefixed_value("Maximum capacity", _system_profiler_values("SPPowerDataType", "Maximum Capacity")),
        ]
        return "; ".join(part for part in parts if part) or UNAVAILABLE

    if system == "Linux":
        rows = []
        for battery_dir in Path("/sys/class/power_supply").glob("BAT*"):
            health = _read_first_line(str(battery_dir / "health"))
            cycle_count = _read_first_line(str(battery_dir / "cycle_count"))
            full = _read_first_line(str(battery_dir / "energy_full"))
            design = _read_first_line(str(battery_dir / "energy_full_design"))
            if full == UNAVAILABLE:
                full = _read_first_line(str(battery_dir / "charge_full"))
            if design == UNAVAILABLE:
                design = _read_first_line(str(battery_dir / "charge_full_design"))

            parts = []
            if health != UNAVAILABLE:
                parts.append(f"health {health}")
            if cycle_count != UNAVAILABLE:
                parts.append(f"{cycle_count} cycles")
            if full.isdigit() and design.isdigit() and int(design) > 0:
                parts.append(f"{int(full) / int(design) * 100:.0f}% of design capacity")
            if parts:
                rows.append(f"{battery_dir.name}: {', '.join(parts)}")
        return "\n".join(rows) if rows else UNAVAILABLE

    if system == "Windows":
        rows = _parse_json_rows(
            _powershell_command(
                "Get-CimInstance Win32_Battery | "
                "Select-Object Name,EstimatedChargeRemaining,BatteryStatus | ConvertTo-Json -Compress"
            )
        )
        formatted = []
        for row in rows:
            name = _normalize(row.get("Name"), "Battery")
            charge = _normalize(row.get("EstimatedChargeRemaining"), "")
            status = _windows_battery_status(_normalize(row.get("BatteryStatus"), ""))
            parts = [part for part in [f"{charge}% charge" if charge else "", status] if part]
            if parts:
                formatted.append(f"{name}: {', '.join(parts)}")
        return "\n".join(formatted) if formatted else UNAVAILABLE

    return UNAVAILABLE


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

    value = _normalize(_windows_cim_snapshot_section("BaseBoard").get(property_name), "")
    if value:
        return value
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

    value = _normalize(_windows_cim_snapshot_section("BIOS").get(property_name), "")
    if value:
        return value
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

    snapshot_disks = _windows_cim_snapshot().get("Disks")
    if isinstance(snapshot_disks, dict):
        snapshot_disks = [snapshot_disks]
    if isinstance(snapshot_disks, list):
        value = _format_disk_rows([row for row in snapshot_disks if isinstance(row, dict)])
        if value != UNAVAILABLE:
            return value

    ps_value = _powershell_command(
        "Get-CimInstance Win32_DiskDrive | "
        "Select-Object Model,SerialNumber | ConvertTo-Json -Compress"
    )
    return _format_disk_rows(_parse_json_rows(ps_value))


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


def _active_network_adapter() -> str:
    if psutil is None:
        return UNAVAILABLE
    try:
        stats = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
    except Exception:
        return UNAVAILABLE

    rows = []
    for adapter_name, adapter_stats in stats.items():
        if not adapter_stats.isup:
            continue
        ipv4_values = [
            address.address
            for address in addresses.get(adapter_name, [])
            if address.family == socket.AF_INET and not address.address.startswith("127.")
        ]
        if ipv4_values:
            rows.append(f"{adapter_name}: {', '.join(ipv4_values)}")
    return "\n".join(rows) if rows else UNAVAILABLE


def _wifi_ssid() -> str:
    system = platform.system()
    if system == "Windows":
        output = _command_output(["netsh", "wlan", "show", "interfaces"])
        for line in output.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "SSID":
                return _normalize_ssid(value)
        return UNAVAILABLE

    if system == "Darwin":
        hidden_by_os = False

        for device in _mac_wifi_devices():
            value = _mac_wifi_ssid_from_ipconfig(device)
            if value not in {UNAVAILABLE, HIDDEN_BY_OS}:
                return value
            hidden_by_os = hidden_by_os or value == HIDDEN_BY_OS

            output = _command_output(["networksetup", "-getairportnetwork", device])
            prefix = "Current Wi-Fi Network:"
            if output.startswith(prefix):
                value = _normalize_ssid(output.removeprefix(prefix))
                if value not in {UNAVAILABLE, HIDDEN_BY_OS}:
                    return value
                hidden_by_os = hidden_by_os or value == HIDDEN_BY_OS

        if hidden_by_os:
            return HIDDEN_BY_OS

        airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
        output = _command_output([airport, "-I"])
        for line in output.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip() == "SSID":
                value = _normalize_ssid(value)
                if value not in {UNAVAILABLE, HIDDEN_BY_OS}:
                    return value
                hidden_by_os = hidden_by_os or value == HIDDEN_BY_OS
        return HIDDEN_BY_OS if hidden_by_os else UNAVAILABLE

    if system == "Linux":
        if shutil.which("nmcli"):
            output = _command_output(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
            for line in output.splitlines():
                active, separator, ssid = line.partition(":")
                if separator and active == "yes":
                    return _normalize_ssid(ssid.replace("\\:", ":"))
        if shutil.which("iwgetid"):
            return _normalize_ssid(_command_output(["iwgetid", "-r"]))

    return UNAVAILABLE


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
        with urllib.request.urlopen(request, timeout=PUBLIC_IP_TIMEOUT_SECONDS) as response:
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


def _display_resolution_refresh_rate() -> str:
    system = platform.system()
    if system == "Darwin":
        resolution = _system_profiler_values("SPDisplaysDataType", "Resolution")
        refresh_rate = _system_profiler_values("SPDisplaysDataType", "Refresh Rate")
        parts = [
            _prefixed_value("Resolution", resolution),
            _prefixed_value("Refresh rate", refresh_rate),
        ]
        return "; ".join(part for part in parts if part) or UNAVAILABLE

    if system == "Linux":
        output = _command_output(["xrandr", "--query"])
        rows = []
        for line in output.splitlines():
            if "*" in line:
                parts = line.split()
                if parts:
                    rows.append(" ".join(parts[:2]))
        if rows:
            return "; ".join(rows)

    if system == "Windows":
        rows = _parse_json_rows(
            _powershell_command(
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate | "
                "ConvertTo-Json -Compress"
            )
        )
        formatted = []
        for row in rows:
            name = _normalize(row.get("Name"), "Display")
            width = _normalize(row.get("CurrentHorizontalResolution"), "")
            height = _normalize(row.get("CurrentVerticalResolution"), "")
            refresh = _normalize(row.get("CurrentRefreshRate"), "")
            if width and height:
                suffix = f" @ {refresh} Hz" if refresh else ""
                formatted.append(f"{name}: {width}x{height}{suffix}")
        return "; ".join(formatted) if formatted else UNAVAILABLE

    return UNAVAILABLE


def _system_architecture() -> str:
    parts = [
        _normalize(platform.machine(), ""),
        _normalize(platform.architecture()[0], ""),
    ]
    return " / ".join(part for part in parts if part) or UNAVAILABLE


def _virtualization_status() -> str:
    system = platform.system()
    if system == "Windows":
        rows = _parse_json_rows(
            _powershell_command(
                "Get-CimInstance Win32_ComputerSystem | "
                "Select-Object Manufacturer,Model,HypervisorPresent | ConvertTo-Json -Compress"
            )
        )
        if rows:
            row = rows[0]
            manufacturer = _normalize(row.get("Manufacturer"), "")
            model = _normalize(row.get("Model"), "")
            hypervisor = _normalize(row.get("HypervisorPresent"), "").lower() == "true"
            detected = _detect_virtual_vendor(f"{manufacturer} {model}")
            if hypervisor or detected:
                detail = detected or f"{manufacturer} {model}".strip()
                return f"Detected ({detail})" if detail else "Detected"
            return "Not detected"

    if system == "Linux":
        if shutil.which("systemd-detect-virt"):
            try:
                completed = subprocess.run(
                    ["systemd-detect-virt"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                value = _normalize(completed.stdout, "")
                if completed.returncode == 0 and value:
                    return f"Detected ({value})"
                if value == "none":
                    return "Not detected"
            except Exception:
                pass
        detected = _detect_virtual_vendor(
            " ".join(
                value
                for value in [
                    _read_first_line("/sys/class/dmi/id/sys_vendor"),
                    _read_first_line("/sys/class/dmi/id/product_name"),
                ]
                if value != UNAVAILABLE
            )
        )
        return f"Detected ({detected})" if detected else "Not detected"

    if system == "Darwin":
        value = _command_output(["sysctl", "-n", "kern.hv_vmm_present"])
        if value == "1":
            return "Detected"
        if value == "0":
            return "Not detected"

    return UNAVAILABLE


def _secure_boot_status() -> str:
    system = platform.system()
    if system == "Windows":
        return _powershell_command(
            "try { if (Confirm-SecureBootUEFI) { 'Enabled' } else { 'Disabled' } } "
            "catch { 'Unavailable' }"
        )

    if system == "Linux":
        if shutil.which("mokutil"):
            output = _command_output(["mokutil", "--sb-state"])
            if output != UNAVAILABLE:
                return output
        return "UEFI detected" if Path("/sys/firmware/efi").exists() else UNAVAILABLE

    return UNAVAILABLE


def _tpm_status() -> str:
    system = platform.system()
    if system == "Windows":
        rows = _parse_json_rows(
            _powershell_command(
                "Get-Tpm | Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated | ConvertTo-Json -Compress"
            )
        )
        if rows:
            row = rows[0]
            present = _normalize(row.get("TpmPresent"), "")
            ready = _normalize(row.get("TpmReady"), "")
            enabled = _normalize(row.get("TpmEnabled"), "")
            activated = _normalize(row.get("TpmActivated"), "")
            return f"Present: {present}; Ready: {ready}; Enabled: {enabled}; Activated: {activated}"
        return UNAVAILABLE

    if system == "Linux":
        tpm_devices = sorted(Path("/sys/class/tpm").glob("tpm*"))
        if tpm_devices:
            return "; ".join(device.name for device in tpm_devices)

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


def _system_volume_path() -> str:
    return str(Path.home().anchor or "/")


def _prefixed_value(prefix: str, value: str) -> str:
    return f"{prefix}: {value}" if value != UNAVAILABLE else ""


def _format_duration(total_seconds: int) -> str:
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _windows_battery_status(value: str) -> str:
    statuses = {
        "1": "discharging",
        "2": "plugged in",
        "3": "fully charged",
        "4": "low",
        "5": "critical",
        "6": "charging",
        "7": "charging high",
        "8": "charging low",
        "9": "charging critical",
        "10": "undefined",
        "11": "partially charged",
    }
    return statuses.get(value, "")


def _mac_wifi_devices() -> list[str]:
    output = _command_output(["networksetup", "-listallhardwareports"])
    if output == UNAVAILABLE:
        return ["en0", "en1"]

    devices: list[str] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() in {"Hardware Port: Wi-Fi", "Hardware Port: AirPort"}:
            for device_line in lines[index + 1 : index + 4]:
                key, separator, value = device_line.partition(":")
                if separator and key.strip() == "Device":
                    devices.append(value.strip())
                    break
    return devices or ["en0", "en1"]


def _mac_wifi_ssid_from_ipconfig(device: str) -> str:
    output = _command_output(["ipconfig", "getsummary", device])
    if output == UNAVAILABLE:
        return UNAVAILABLE

    for line in output.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "SSID":
            return _normalize_ssid(value)
    return UNAVAILABLE


def _normalize_ssid(value: str) -> str:
    text = _normalize(value)
    if text == UNAVAILABLE:
        return UNAVAILABLE
    lowered = text.casefold()
    if text == "<redacted>" or "redacted" in lowered:
        return HIDDEN_BY_OS
    if "not associated" in lowered or "not connected" in lowered:
        return UNAVAILABLE
    return text


def _detect_virtual_vendor(value: str) -> str:
    lowered = value.lower()
    vendors = {
        "vmware": "VMware",
        "virtualbox": "VirtualBox",
        "kvm": "KVM",
        "qemu": "QEMU",
        "hyper-v": "Hyper-V",
        "microsoft corporation virtual": "Hyper-V",
        "parallels": "Parallels",
        "xen": "Xen",
        "bhyve": "bhyve",
    }
    for marker, label in vendors.items():
        if marker in lowered:
            return label
    return ""


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


def _powershell_command(command: str, timeout: int = 8) -> str:
    if platform.system() != "Windows":
        return UNAVAILABLE

    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
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
