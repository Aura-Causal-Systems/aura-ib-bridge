"""PyInstaller entry point: imports the tray app as a package so its relative imports work."""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ib_bridge.tray import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
