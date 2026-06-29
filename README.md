# System Info Checker

System Info Checker is a safe, read-only desktop GUI app for viewing common system, hardware, network, and runtime information on Windows, macOS, and Linux.

It does **not** spoof, hide, randomize, bypass, or change hardware identifiers. It only displays information gathered through normal read-only APIs, shell tools, system files, PowerShell/CIM queries, and read-only registry access where available.

## Features

- Modern PySide6 dashboard interface with summary cards and sectioned details
- Search system details without leaving the main view
- Quickly refresh system information without restarting the app
- Copy individual details or the full report to the clipboard
- Save the full report as a `.txt` file
- Gracefully shows `Unavailable` when a field cannot be read
- Does not require administrator privileges
- Cross-platform launch scripts for Windows, macOS, and Linux
- Organized for future PyInstaller packaging

## Information Displayed

- Username
- Computer name
- OS version/build
- CPU name
- GPU name
- RAM amount
- Board/system manufacturer, product, and serial when available
- Firmware/BIOS serial and version when available
- Disk model and serial when available
- MAC addresses for network adapters
- Local IP address
- Public IP address, when safely reachable through `https://api.ipify.org`
- Machine identifier when available
- Python version
- App run time/date

## Requirements

- Windows 10 or newer, macOS, or Linux
- Python 3.10 or newer

## Quick Start

Download or clone the project.

On Windows, double-click:

```text
run.bat
```

On macOS or Linux, run:

```sh
chmod +x run.sh
./run.sh
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

On Windows, open PowerShell in the project folder and run:

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

On macOS or Linux, open a terminal in the project folder and run:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

## Package Later With PyInstaller

After installing dependencies, you can create a single executable with:

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name "System Info Checker" main.py
```

On Windows, the packaged app will be created under `dist\`. On macOS and Linux, it will be created under `dist/`.

## Safety Notes

This project is intentionally read-only:

- No registry writes
- No hardware identifier changes
- No spoofing or randomization
- No anti-cheat, licensing, ban evasion, or bypass behavior
- No administrator privileges required

Some fields depend on OS APIs, firmware support, adapter settings, installed command-line tools, or permission boundaries. If the operating system does not expose a value, the app displays `Unavailable`.
