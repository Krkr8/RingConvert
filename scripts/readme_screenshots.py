#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the README's ring pictures (docs/screenshots/), off-screen.

    python3 scripts/readme_screenshots.py [OUT_DIR]

The dialog pictures come from scripts/snapshot_dialogs.py (best run inside the
headless test shell: python3 tests/shell_harness.py, then the XDG_RUNTIME_DIR=…
WAYLAND_DISPLAY=… DBUS_SESSION_BUS_ADDRESS=… it prints).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cairo  # noqa: E402

from ringconvert.core.detect import ALL_TOOLS, FileInfo, label, targets  # noqa: E402
from ringconvert.ring_widget import RingState, Segment, render_png  # noqa: E402


def backdrop(cr, w, h):
    # A soft desktop-like wash so the glass disc reads as it does on screen.
    gradient = cairo.LinearGradient(0, 0, w, h)
    gradient.add_color_stop_rgb(0, 0.36, 0.42, 0.58)
    gradient.add_color_stop_rgb(1, 0.62, 0.48, 0.55)
    cr.set_source(gradient)
    cr.paint()


def ring(path: Path, segments: list[Segment], hovered: int, readout: str) -> None:
    hover = [1.0 if i == hovered else 0.0 for i in range(len(segments))]
    render_png(str(path), RingState(segments=segments, hover=hover, readout=readout), background=backdrop)
    print(path)


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    formats = targets(FileInfo(Path("Photo.jpg"), "image/jpeg", "image", "jpg"))
    convert = [Segment(f, label(f)) for f in formats]
    ring(out / "convert-ring.png", convert, formats.index("tiff"), "TIFF")
    tools = [Segment(t, label(t), icon=t) for t in ALL_TOOLS]
    ring(out / "tools-ring.png", tools, ALL_TOOLS.index("crop"), "CROP")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/screenshots"))
