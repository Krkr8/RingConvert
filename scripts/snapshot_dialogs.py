#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render tool dialogs to PNG without user input (README pictures, visual checks).

    python3 scripts/snapshot_dialogs.py OUT_DIR FILE [TOOL ...]

TOOL defaults to every tool with a dialog. Each dialog is opened on FILE and
its content saved as OUT_DIR/<tool>.png (transparent outside the panel).
Opens real windows briefly on the current display; uses a private app ID so
a running RingConvert isn't affected.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from ringconvert.app import RingConvertApp  # noqa: E402
from ringconvert.dialogs import DIALOGS, open_dialog  # noqa: E402


def snapshot(window: Gtk.Window, path: Path) -> None:
    # A window's own paintable yields no render node: snapshot its content.
    widget = window.get_content()
    paintable = Gtk.WidgetPaintable.new(widget)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, paintable.get_intrinsic_width(), paintable.get_intrinsic_height())
    node = snap.to_node()
    if node is None:
        raise RuntimeError(f"{path.stem}: nothing drawn yet")
    window.get_renderer().render_texture(node, None).save_to_png(str(path))
    print(path, flush=True)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out, src = Path(sys.argv[1]), Path(sys.argv[2])
    tools = sys.argv[3:] or list(DIALOGS)
    out.mkdir(parents=True, exist_ok=True)
    app = RingConvertApp()
    app.set_application_id("io.github.krkr8.Snapshot")
    pending = list(tools)
    status = {"code": 0}

    def step() -> bool:
        # One dialog per tick: open it, give it time to draw, save, close.
        if not pending:
            app.quit()
            return GLib.SOURCE_REMOVE
        tool = pending.pop(0)
        dialog = open_dialog(app, tool, [src])
        if dialog is None:
            status["code"] = 1
            return GLib.SOURCE_CONTINUE

        def save() -> bool:
            try:
                snapshot(dialog, out / f"{tool}.png")
            except Exception as exc:
                print(f"{tool}: {exc}", file=sys.stderr, flush=True)
                status["code"] = 1
            dialog.close()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(700, save)
        return GLib.SOURCE_CONTINUE

    def start(*_args) -> int:
        app.hold()
        GLib.timeout_add(900, step)
        return 0

    app.connect("command-line", start)
    app.run([sys.argv[0]])
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
