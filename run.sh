#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

printf "\nSystem Info Checker\n"
printf "===================\n\n"

PYTHON_CMD=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    printf "Python 3.10 or newer was not found.\n"
    printf "Install Python from https://www.python.org/downloads/\n"
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    printf "Creating local virtual environment...\n"
    "$PYTHON_CMD" -m venv .venv
fi

printf "Checking dependencies...\n"
if ! ./.venv/bin/python -c "import PySide6, psutil" >/dev/null 2>&1; then
    printf "Installing dependencies...\n"
    ./.venv/bin/python -m pip install --upgrade pip
    ./.venv/bin/python -m pip install -r requirements.txt
fi

printf "\nStarting app...\n"
exec ./.venv/bin/python main.py
