# System Info Checker

System Info Checker is a safe, read-only Windows desktop GUI app for viewing common system, hardware, network, and runtime information.

It does **not** spoof, hide, randomize, bypass, or change hardware identifiers. It only displays information gathered through normal read-only APIs, PowerShell/CIM queries, and read-only registry access where available.

## Features

- Modern dark PySide6 desktop interface
- Refresh system information without restarting the app
- Copy the full report to the clipboard
- Save the full report as a `.txt` file
- Gracefully shows `Unavailable` when a field cannot be read
- Does not require administrator privileges
- Organized for future PyInstaller packaging

## Information Displayed

- Windows username
- Computer name
- Windows version/build
- CPU name
- GPU name
- RAM amount
- Motherboard manufacturer, product, and serial when available
- BIOS serial and version when available
- Disk model and serial when available
- MAC addresses for network adapters
- Local IP address
- Public IP address, when safely reachable through `https://api.ipify.org`
- Machine GUID / HWID-style identifier from the normal Windows registry location when available
- Python version
- App run time/date

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or newer

## Quick Start

Download or clone the project, then double-click:

```text
run.bat
```

The launcher creates a local `.venv`, installs the required packages, and starts the app.

If Windows SmartScreen or your browser warns about downloaded scripts, review the file first. It only creates a Python virtual environment, installs `requirements.txt`, and runs `main.py`.

## Run From PowerShell

You can also run:

```powershell
.\run.ps1
```

If PowerShell blocks scripts on your system, use:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

## Manual Run

Open PowerShell in the project folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

If PowerShell blocks virtual environment activation, run this once for the current PowerShell session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Package Later With PyInstaller

After installing dependencies, you can create a single executable with:

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name "System Info Checker" main.py
```

The packaged app will be created under `dist\`.

## Safety Notes

This project is intentionally read-only:

- No registry writes
- No hardware identifier changes
- No spoofing or randomization
- No anti-cheat, licensing, ban evasion, or bypass behavior
- No administrator privileges required

Some fields depend on Windows APIs, firmware support, adapter settings, or permission boundaries. If Windows does not expose a value, the app displays `Unavailable`.
