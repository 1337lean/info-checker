@echo off
setlocal
cd /d "%~dp0"
title System Info Checker

echo.
echo System Info Checker
echo ===================
echo.

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=python"
    ) else (
        echo Python 3.10 or newer was not found.
        echo Install Python from https://www.python.org/downloads/windows/
        echo Make sure "Add python.exe to PATH" is checked during install.
        echo.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto fail
)

echo Checking dependencies...
".venv\Scripts\python.exe" -c "import PySide6, psutil" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto fail

    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)

echo.
echo Starting app...
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto fail

exit /b 0

:fail
echo.
echo Something went wrong while starting System Info Checker.
echo You can copy the messages above when asking for help.
echo.
pause
exit /b 1
