# SPDX-License-Identifier: GPL-3.0-or-later
"""The undecorated, transparent window holding the ring; a drag-and-drop target."""

from __future__ import annotations

import os
from pathlib import Path

import cairo
import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .core import compress  # noqa: E402
from .core.detect import ALL_TOOLS, FileInfo, common_targets, common_tools, detect, label  # noqa: E402
from .core.naming import Result  # noqa: E402
from .debug import debug  # noqa: E402
from .dialogs import DIALOGS  # noqa: E402
from .ring_widget import DARK, WARM, RingPainter, RingWidget, Segment  # noqa: E402

# Detecting a huge selection would stall the drag; the menu only needs a sample.
MAX_DETECT = 200
# Window titles per mode; gnome-extension/…/extension.js matches them.
RING_TITLES = {"convert": "RingConvert", "tools": "RingConvert Tools"}


def _compress(path: Path, progress) -> Result:
    return compress.compress(detect(path), progress)


# Tools that run straight from the ring like a conversion (no dialog).
DIRECT_TOOLS = {"compress": _compress}
# Tools that open a dialog (dialogs.DIALOGS); anything else is shown dimmed.
DIALOG_TOOLS = set(DIALOGS)
# Tools whose dialog edits one file at a time.
SINGLE_FILE_TOOLS = {"crop", "edit", "annotate", "redact"}


def tool_segments(tools: list[str], count: int) -> list[Segment]:
    """Tools-ring segments for `count` files (0 = nothing dropped yet)."""
    def enabled(tool: str) -> bool:
        if tool in SINGLE_FILE_TOOLS and count > 1:
            return False
        return tool in DIALOG_TOOLS or tool in DIRECT_TOOLS

    return [Segment(t, label(t), icon=t, enabled=enabled(t)) for t in tools]


def _file_list(value) -> list[Gio.File] | None:
    """Files from a DropTarget value (a Gdk.FileList, possibly still boxed)."""
    if isinstance(value, GObject.Value):
        value = value.get_value()
    if isinstance(value, Gdk.FileList):
        return value.get_files()
    return None


def _palette():
    # The warm look is the default even in dark mode;
    # RINGCONVERT_THEME=system follows the desktop's light/dark preference.
    if os.environ.get("RINGCONVERT_THEME") == "system" and Adw.StyleManager.get_default().get_dark():
        return DARK
    return WARM


class RingWindow(Gtk.Window):
    def __init__(self, app, stay: bool = False, mode: str = "convert"):
        super().__init__(application=app, title="RingConvert", decorated=False, resizable=False)
        self.app = app
        self.stay = stay
        self.add_css_class("ringconvert-ring")
        self.ring = RingWidget(RingPainter(palette=_palette()))
        size = self.ring.geo.size
        self.set_default_size(size, size)
        self.set_child(self.ring)

        self.mode = mode  # "convert" or "tools"; Shift switches
        self.paths: list[Path] = []
        self.files_given = False
        self.infos: list[FileInfo] = []
        self.items: list[str] = []
        self.size_text = ""
        self.status = "Drop a file"
        self.busy = False
        self._job_done = False
        self._pulse_done = False

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.set_preload(True)  # read the file list on hover to build the menu
        drop.connect("notify::value", self._on_drop_value)
        drop.connect("enter", self._on_drop_hover)
        drop.connect("motion", self._on_drop_hover)
        drop.connect("drop", self._on_drop)
        self.ring.add_controller(drop)

        drag_motion = Gtk.DropControllerMotion()
        drag_motion.connect("enter", lambda _c, x, y: self._hover_at(x, y))
        drag_motion.connect("motion", lambda _c, x, y: self._hover_at(x, y))
        drag_motion.connect("leave", lambda _c: self._hover_at(None, None))
        self.ring.add_controller(drag_motion)

        # Without a drag (files passed on the command line / "Open With"),
        # hovering and clicking a segment works too.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _c, x, y: self._hover_at(x, y) if self.paths else None)
        motion.connect("motion", lambda _c, x, y: self._hover_at(x, y) if self.paths else None)
        motion.connect("leave", lambda _c: self._hover_at(None, None))
        self.ring.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.ring.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.connect("realize", self._on_realize)
        self._rebuild_menu()  # the tools ring has segments even before a file arrives

    # ------------------------------------------------------------ setup

    def _on_realize(self, _window) -> None:
        # Only the disc takes input, so clicks and drops in the transparent
        # corners reach whatever is underneath.
        g = self.ring.geo
        cx, cy = g.center
        region = cairo.Region()
        step = 2
        for y in range(int(cy - g.radius), int(cy + g.radius) + 1, step):
            dy = min(abs(y - cy), abs(y + step - cy))
            if dy > g.radius:
                continue
            half = (g.radius ** 2 - dy ** 2) ** 0.5
            region.union(cairo.RectangleInt(int(cx - half), y, int(2 * half) + 1, step))
        surface = self.get_surface()
        if surface:
            surface.set_input_region(region)

    @property
    def shown(self) -> bool:
        """Visible and not on its way out."""
        return self.get_visible() and not self.ring.closing

    def present_ring(self, files: list[Gio.File] | None = None, mode: str | None = None) -> None:
        debug("present_ring files=%s mode=%s (current mode=%s busy=%s)",
              [f.get_uri() for f in files or []], mode, self.mode, self.busy)
        if mode and mode != self.mode and not self.busy:
            self.mode = mode
            self._rebuild_menu()
        if files:
            self.set_files(files)
        self.ring.appear()
        self.present()

    # ------------------------------------------------------------ files & menu

    def set_files(self, files: list[Gio.File]) -> None:
        if self.busy:
            debug("set_files ignored: busy")
            return
        paths = [Path(p) for p in (f.get_path() for f in files) if p]
        if [str(p) for p in paths] == [str(p) for p in self.paths]:
            return
        self.paths = paths
        self.files_given = bool(files)
        self.infos = [detect(p) for p in paths[:MAX_DETECT] if p.is_file()]
        total = sum(p.stat().st_size for p in paths if p.is_file())
        self.size_text = GLib.format_size(total) if self.infos else ""
        debug("set_files paths=%s detected=%s", [str(p) for p in paths],
              [(i.kind, i.format) for i in self.infos])
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        tools = self.mode == "tools"
        if not self.files_given:
            # Nothing dropped yet: the tools ring is a fixed menu, the convert
            # ring shows empty petals until the file type is known.
            self.items, self.status = (list(ALL_TOOLS) if tools else []), "Drop a file"
        elif not self.paths:
            self.items, self.status = [], "Not a local file"
        elif not self.infos:
            self.items, self.status = [], "Not a file"
        elif tools:
            self.items = common_tools(self.infos)
            self.status = "" if self.items else "No tools"
        else:
            self.items = common_targets(self.infos)
            self.status = "" if self.items else "Can't convert"
        # The Shell extension finds the ring by this title and reads the mode from it.
        self.set_title(RING_TITLES[self.mode])
        if tools:
            count = len(self.paths) if self.files_given else 0
            self.ring.set_segments(tool_segments(self.items, count))
        else:
            self.ring.set_segments([Segment(item, label(item)) for item in self.items])
        self._update_readout()

    def set_mode(self, mode: str) -> None:
        if self.busy or mode == self.mode:
            return
        self.mode = mode
        self._rebuild_menu()
        self.ring.appear()  # re-run the scale + fade so the switch reads clearly

    def toggle_mode(self) -> None:
        self.set_mode("convert" if self.mode == "tools" else "tools")

    def _usable(self, index: int | None) -> bool:
        segments = self.ring.state.segments
        return index is not None and index < len(self.items) and segments[index].enabled

    def _update_readout(self) -> None:
        if self.busy:
            return
        hovered = self.ring.hovered
        if hovered is not None and hovered < len(self.items):
            self.ring.set_readout(label(self.items[hovered]))
        else:
            self.ring.set_readout(self.status or self.size_text)

    def _hover_at(self, x, y) -> None:
        if self.busy:
            return
        before = self.ring.hovered
        self.ring.set_hovered(None if x is None else self.ring.hit(x, y))
        if self.ring.hovered != before:
            debug("hover (%s, %s) -> segment %s", None if x is None else round(x),
                  None if y is None else round(y), self.ring.hovered)
        self._update_readout()

    # ------------------------------------------------------------ drag and drop

    def _on_drop_value(self, target, _pspec) -> None:
        files = _file_list(target.get_value())
        if files is not None:
            self.set_files(files)

    def _on_drop_hover(self, _target, x, y):
        self._hover_at(x, y)
        if self.busy:
            return 0
        # Accept anywhere on the disc so a release on the hub still reaches us
        # (and closes the ring) instead of silently cancelling.
        return Gdk.DragAction.COPY if self.ring.geo.in_disc(x, y) else 0

    def _on_drop(self, _target, value, x, y) -> bool:
        debug("drop at (%d, %d) busy=%s value=%s", x, y, self.busy, type(value).__name__)
        if self.busy:
            return False
        files = _file_list(value)
        if files is not None:
            self.set_files(files)
        index = self.ring.hit(x, y)
        if not self._usable(index):
            self.dismiss()
            return False
        self._start(index)
        return True

    def _on_click(self, _gesture, _n, x, y) -> None:
        index = self.ring.hit(x, y)
        debug("click at (%d, %d) size=%dx%d -> segment %s usable=%s in_disc=%s paths=%d busy=%s",
              x, y, self.ring.get_width(), self.ring.get_height(), index, self._usable(index),
              self.ring.geo.in_disc(x, y), len(self.paths), self.busy)
        if self.busy:
            return
        if self._usable(index) and self.paths:
            self._start(index)
        elif not self.ring.geo.in_disc(x, y) or index is None:
            self.dismiss()

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        debug("key %s", Gdk.keyval_name(keyval))
        if keyval == Gdk.KEY_Escape:
            self.dismiss()
            return True
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R):
            self.toggle_mode()
            return True
        return False

    # ------------------------------------------------------------ jobs

    def _start(self, index: int) -> None:
        target = self.items[index]
        if self.mode == "tools" and target not in DIRECT_TOOLS:
            self._start_tool(index)
            return
        self.busy = True
        self._job_done = self._pulse_done = False
        self.ring.set_hovered(index)
        self.ring.set_readout(label(target))
        self.ring.pulse(index, then=self._on_pulse_done)
        work = DIRECT_TOOLS.get(target) if self.mode == "tools" else None
        self.app.start_job(list(self.paths), target, self, work)

    def _start_tool(self, index: int) -> None:
        # Tools open a dialog: pulse, close the ring, then show the dialog.
        tool, paths = self.items[index], list(self.paths)
        self.busy = True
        self.ring.set_hovered(index)
        self.ring.set_readout(label(tool))

        def open_dialog() -> bool:
            self.busy = False
            self.dismiss()
            self.app.open_tool(paths, tool)
            return GLib.SOURCE_REMOVE

        self.ring.pulse(index, then=open_dialog)

    def _on_pulse_done(self) -> bool:
        self._pulse_done = True
        if self._job_done:
            self._finish()
        else:
            # Still working: keep the ring up with progress in the hub.
            self.ring.set_progress(-1)
            self.ring.set_readout("…")
        return GLib.SOURCE_REMOVE

    def job_progress(self, fraction: float) -> None:
        if self._pulse_done and not self._job_done:
            self.ring.set_progress(fraction)
            self.ring.set_readout(f"{int(fraction * 100)}%")

    def job_finished(self) -> None:
        self._job_done = True
        if self._pulse_done:
            self._finish()

    def _finish(self) -> None:
        self.ring.set_progress(None)
        self.busy = False
        self.dismiss()

    def dismiss(self) -> None:
        debug("dismiss")
        self.ring.dismiss(then=self._after_dismiss)

    def _after_dismiss(self) -> bool:
        debug("after dismiss: busy=%s stay=%s", self.busy, self.stay)
        if self.busy:
            # Escape during a long job: hide; the notification reports the end.
            self.set_visible(False)
        elif self.stay:
            self.set_files([])
            self.ring.set_hovered(None)
            self.ring.appear()
        else:
            self.close()
        return GLib.SOURCE_REMOVE
