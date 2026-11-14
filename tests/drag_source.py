# SPDX-License-Identifier: GPL-3.0-or-later
"""A maximized window that drags FILE (as a Gdk.FileList) wherever it is
pressed: the "Files" side of a drag in the headless-shell tests."""

import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GObject, Gtk  # noqa: E402


def main(path: str) -> int:
    app = Gtk.Application(application_id="io.github.krkr8.TestDragSource",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(_app):
        window = Gtk.ApplicationWindow(application=app, title="Drag source")
        area = Gtk.Label(label=f"drag {path}", hexpand=True, vexpand=True)
        source = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        files = Gdk.FileList.new_from_list([Gio.File.new_for_path(path)])
        source.set_content(Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, files)))
        source.connect("drag-begin", lambda *_: print("drag-begin", flush=True))
        source.connect("drag-end", lambda _s, _d, deleted: print(f"drag-end deleted={deleted}", flush=True))
        source.connect("drag-cancel", lambda _s, _d, reason: print(f"drag-cancel {reason}", flush=True))
        area.add_controller(source)
        window.set_child(area)
        window.connect("map", lambda *_: print("mapped", flush=True))
        window.maximize()
        window.present()

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
