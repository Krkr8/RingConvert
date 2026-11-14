# SPDX-License-Identifier: GPL-3.0-or-later
"""Files (Nautilus) right-click menu: "Convert with RingConvert ▸".

The submenu lists the formats every selected file can be converted to (batch
convert for several files) and a "Tools ▸" submenu, exactly as the rings would
show them. Choosing an entry calls the RingConvert app over D-Bus (which starts
it if needed), so the job runs there and reports through a notification; tools
with a dialog (Crop, Edit…) open it.

Installed by install.sh to ~/.local/share/nautilus-python/extensions/ (needs
the nautilus-python package); Files loads it after `nautilus -q`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Nautilus", "4.1")
from gi.repository import Gio, GLib, GObject, Nautilus  # noqa: E402

try:
    from ringconvert.core.detect import common_targets, common_tools, detect, label
except ImportError:  # Files' Python may not see the user's site-packages: use the source tree
    sys.path.insert(0, "@RINGCONVERT_SRC@")  # install.sh fills this in
    from ringconvert.core.detect import common_targets, common_tools, detect, label

APP_ID = os.environ.get("RINGCONVERT_APP_ID", "io.github.krkr8.RingConvert")
OBJECT_PATH = "/" + APP_ID.replace(".", "/")
# Mirrors ring_window: dialogs that edit one file at a time.
SINGLE_FILE_TOOLS = {"crop", "edit", "annotate", "redact"}
# "…" marks the ones that open a dialog.
TOOL_LABELS = {"compress": "Compress", "metadata": "Metadata…", "edit": "Edit…",
               "annotate": "Annotate…", "add_bg": "Add Background…", "crop": "Crop…",
               "redact": "Redact…"}
# Menus are built on every right-click: never inspect a huge selection.
MAX_FILES = 200
# The first call may start the app (a Python start-up).
CALL_TIMEOUT_MS = 30000


def _local_path(file) -> Path | None:
    if file.is_directory():
        return None
    location = file.get_location()
    path = location.get_path() if location else None
    return Path(path) if path else None


def menu_entries(files) -> tuple[list[str], list[tuple[str, bool]]]:
    """(formats, [(tool, enabled)]) for a selection of Nautilus.FileInfo."""
    if not files or len(files) > MAX_FILES:
        return [], []
    infos = []
    for file in files:
        path = _local_path(file)
        if path is None:
            return [], []
        info = detect(path, file.get_mime_type() or None)
        if info.kind is None:
            return [], []
        infos.append(info)
    batch = len(infos) > 1
    tools = [(tool, not (batch and tool in SINGLE_FILE_TOOLS)) for tool in common_tools(infos)]
    return common_targets(infos), tools


def call_app(method: str, uris: list[str], name: str) -> None:
    def done(bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error as exc:
            print(f"ringconvert-nautilus: {method} {name}: {exc.message}", file=sys.stderr, flush=True)

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as exc:
        print(f"ringconvert-nautilus: no session bus: {exc.message}", file=sys.stderr, flush=True)
        return
    bus.call(APP_ID, OBJECT_PATH, "io.github.krkr8.RingConvert", method,
             GLib.Variant("(ass)", (uris, name)), None, Gio.DBusCallFlags.NONE,
             CALL_TIMEOUT_MS, None, done)


class RingConvertMenu(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        try:
            formats, tools = menu_entries(files)
        except Exception as exc:  # never break the Files context menu
            print(f"ringconvert-nautilus: {exc}", file=sys.stderr, flush=True)
            return []
        if not formats and not tools:
            return []
        uris = [f.get_uri() for f in files]

        def item(name, text, method=None, value=None, sensitive=True):
            entry = Nautilus.MenuItem(name=f"RingConvert::{name}", label=text, sensitive=sensitive)
            if method:
                entry.connect("activate", lambda _item: call_app(method, uris, value))
            return entry

        top = item("menu", "Convert with RingConvert")
        submenu = Nautilus.Menu()
        top.set_submenu(submenu)
        for fmt in formats:
            submenu.append_item(item(f"to-{fmt}", label(fmt), "Convert", fmt))
        if tools:
            tools_item = item("tools", "Tools")
            tools_menu = Nautilus.Menu()
            tools_item.set_submenu(tools_menu)
            for tool, enabled in tools:
                text = TOOL_LABELS.get(tool, label(tool).title())
                tools_menu.append_item(item(f"tool-{tool}", text, "RunTool", tool, enabled))
            submenu.append_item(tools_item)
        return [top]
