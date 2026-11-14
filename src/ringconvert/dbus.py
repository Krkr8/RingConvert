# SPDX-License-Identifier: GPL-3.0-or-later
"""The app's D-Bus interface, used by the GNOME Shell extension and scripts.

    io.github.krkr8.RingConvert  (on the app's bus name and object path)
      Show(i x, i y)       show the convert ring
      ShowTools(i x, i y)  the same for the tools ring
      Hide()               close the ring
      Convert(as uris, s target)  convert local files to `target` (e.g. "jpg") without
                           the ring; a notification reports (the Files right-click menu)
      RunTool(as uris, s tool)    run a tools-ring entry ("compress", "crop", …) on files:
                           opens its dialog, or runs straight away for compress

Apps can't place windows on Wayland: the Shell extension moves every new ring
window, to the (x, y) of its own Show call or else to the cursor (so x, y from
other callers are not honoured).

Try it:  gdbus call --session --dest io.github.krkr8.RingConvert \\
           --object-path /io/github/krkr8/RingConvert \\
           --method io.github.krkr8.RingConvert.Show 0 0
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .debug import debug  # noqa: E402

INTERFACE = "io.github.krkr8.RingConvert"

XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Show">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="ShowTools">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Hide"/>
    <method name="Convert">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="target" direction="in"/>
    </method>
    <method name="RunTool">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="tool" direction="in"/>
    </method>
  </interface>
</node>
"""


class RingService:
    """Exports INTERFACE for `app` (a RingConvertApp) on a connection."""

    def __init__(self, app):
        self.app = app
        self._info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self._registrations: list[tuple[Gio.DBusConnection, int]] = []

    def register(self, connection: Gio.DBusConnection, path: str) -> None:
        reg_id = connection.register_object(path, self._info, self._on_call, None, None)
        self._registrations.append((connection, reg_id))

    def unregister(self) -> None:
        for connection, reg_id in self._registrations:
            connection.unregister_object(reg_id)
        self._registrations.clear()

    def _on_call(self, _connection, sender, _path, _interface, method, params, invocation) -> None:
        debug("D-Bus %s%s from %s", method, params.unpack(), sender)
        try:
            if method in ("Show", "ShowTools"):
                x, y = params.unpack()
                self.app.show_at(x, y, "tools" if method == "ShowTools" else "convert")
            elif method == "Hide":
                self.app.hide_ring()
            elif method in ("Convert", "RunTool"):
                uris, name = params.unpack()
                paths = _local_paths(uris)
                if method == "Convert":
                    self.app.convert_files(paths, name)
                else:
                    self.app.run_menu_tool(paths, name)
            else:
                invocation.return_dbus_error(f"{INTERFACE}.Error.UnknownMethod", method)
                return
        except Exception as exc:  # report instead of leaving the caller hanging
            invocation.return_dbus_error(f"{INTERFACE}.Error.Failed", str(exc))
            return
        invocation.return_value(None)


def _local_paths(uris: list[str]) -> list[Path]:
    paths = []
    for uri in uris:
        path = Gio.File.new_for_uri(uri).get_path()
        if not path:
            raise ValueError(f"not a local file: {uri}")
        paths.append(Path(path))
    if not paths:
        raise ValueError("no files given")
    return paths


def show_in_files(uris: list[str]) -> None:
    """Open the file manager with `uris` selected (falls back to their folder)."""
    def fallback() -> None:
        folder = Gio.File.new_for_uri(uris[0]).get_parent()
        if folder is not None:
            Gio.AppInfo.launch_default_for_uri_async(folder.get_uri(), None, None, None, None)

    def done(bus, result) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as exc:
            debug("FileManager1.ShowItems failed (%s); opening the folder", exc.message)
            fallback()

    if not uris:
        return
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        fallback()
        return
    bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
             "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", (uris, "")),
             None, Gio.DBusCallFlags.NONE, 5000, None, done)
