"""Generates packaging/bridge.ico from the same drawing the tray uses (no binary assets in git)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ib_bridge.tray import COLOR_CONNECTED, render_icon  # noqa: E402

out = Path(__file__).with_name("bridge.ico")
base = render_icon(COLOR_CONNECTED, 256)
base.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {out}")
