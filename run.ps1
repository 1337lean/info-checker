$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "System Info Checker"
Write-Host "==================="
Write-Host ""

$pythonExe = $null
$pythonArgs = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = "py"
    $pythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = "python"
} else {
    Write-Host "Python 3.10 or newer was not found."
    Write-Host "Install Python from https://www.python.org/downloads/windows/"
    Write-Host 'Make sure "Add python.exe to PATH" is checked during install.'
    Read-Host "Press Enter to exit"
    exit 1
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating local virtual environment..."
    & $pythonExe @pythonArgs -m venv .venv
}

Write-Host "Checking dependencies..."
& ".\.venv\Scripts\python.exe" -c "import PySide6, psutil" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..."
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
}

Write-Host ""
Write-Host "Starting app..."
& ".\.venv\Scripts\python.exe" main.py
