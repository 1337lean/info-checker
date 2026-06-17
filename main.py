"""Launcher for System Info Checker."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from system_info_checker.app import main


if __name__ == "__main__":
    raise SystemExit(main())
