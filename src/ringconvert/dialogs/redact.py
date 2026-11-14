# SPDX-License-Identifier: GPL-3.0-or-later
"""Redact dialog: drag black boxes over an image or PDF pages.

PDFs get true redaction (the text, images and vector art under each box are
removed, see core.pdf.redact); images get solid boxes and lose all metadata
(core.imageops.redact), so an EXIF thumbnail can't leak what was hidden.
"""

from __future__ import annotations

import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core import imageops, pdf  # noqa: E402
from . import ToolDialog, icon_button, row  # noqa: E402
from .canvas import DocCanvas, PageNav, Pages  # noqa: E402

Box = tuple[int, float, float, float, float]  # page, x, y, w, h (document units)

MIN_BOX = 3.0  # document units; smaller drags count as clicks

# Overlay colours: placed boxes look like the result (near-black); the box
# being dragged is see-through with a white outline, like the crop frame.
BOX_FILL = (0.04, 0.035, 0.035)
DRAG_FILL = (0.04, 0.035, 0.035, 0.55)
DRAG_OUTLINE = (1.0, 1.0, 1.0, 0.95)


def box_at(boxes: list[Box], page: int, x: float, y: float) -> int | None:
    """Index of the top-most box on `page` containing (x, y)."""
    for i in range(len(boxes) - 1, -1, -1):
        p, bx, by, bw, bh = boxes[i]
        if p == page and bx <= x <= bx + bw and by <= y <= by + bh:
            return i
    return None


def pixel_rect(box: Box) -> tuple[int, int, int, int]:
    """(x, y, w, h) in whole pixels, grown outwards so the box covers every
    pixel it touches (rounding could leave a one-pixel strip visible)."""
    _p, x, y, w, h = box
    left, top = math.floor(x), math.floor(y)
    return left, top, max(1, math.ceil(x + w) - left), max(1, math.ceil(y + h) - top)


def normalise(page: int, x0: float, y0: float, x1: float, y1: float) -> Box:
    return page, min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


class RedactCanvas(DocCanvas):
    def __init__(self, pages: Pages, width: int, height: int, on_change):
        super().__init__(pages, width, height)
        self.boxes: list[Box] = []
        self.on_change = on_change
        self._anchor: tuple[float, float] | None = None
        self._current: Box | None = None
        self.set_cursor_from_name("crosshair")

    def press(self, x, y) -> None:
        self._anchor = (x, y)
        self._current = None

    def drag(self, x, y) -> None:
        if self._anchor:
            self._current = normalise(self.page, *self._anchor, x, y)
            self.queue_draw()

    def release(self, x, y) -> None:
        if not self._anchor:
            return
        box = normalise(self.page, *self._anchor, x, y)
        self._anchor = self._current = None
        if box[3] >= MIN_BOX and box[4] >= MIN_BOX:
            self.boxes.append(box)
        else:
            # A click on a box removes it.
            hit = box_at(self.boxes, self.page, x, y)
            if hit is not None:
                del self.boxes[hit]
        self.queue_draw()
        self.on_change()

    def undo(self) -> None:
        if self.boxes:
            self.boxes.pop()
            self.queue_draw()
            self.on_change()

    def draw_overlay(self, cr) -> None:
        unit = 1 / self.scale()  # one screen pixel in document units
        for page, x, y, w, h in self.boxes:
            if page == self.page:
                cr.rectangle(x, y, w, h)
        cr.set_source_rgb(*BOX_FILL)
        cr.fill()
        if self._current:
            _p, x, y, w, h = self._current
            cr.rectangle(x, y, w, h)
            cr.set_source_rgba(*DRAG_FILL)
            cr.fill_preserve()
            cr.set_source_rgba(*DRAG_OUTLINE)
            cr.set_line_width(1.5 * unit)
            cr.stroke()


class RedactDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        super().__init__(app, "Redact", "Redact", width=560, reset="Clear")
        self.src = paths[0]
        self.pages = Pages(self.src)

        well = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        well.add_css_class("preview-well")
        self.canvas = RedactCanvas(self.pages, 520, 500, self._on_change)
        self.canvas.set_halign(Gtk.Align.CENTER)
        well.append(self.canvas)
        well.append(PageNav(self.canvas))
        self.body.append(well)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls.add_css_class("dialog-controls")
        self.count = Gtk.Label(xalign=0, hexpand=True)
        self.count.add_css_class("dialog-caption")
        self.undo_button = icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", lambda _b: self.canvas.undo())
        controls.append(row("Boxes", self.count, self.undo_button))
        self.body.append(controls)

        if self.pages.is_pdf:
            self.set_note("Text and images under the boxes are removed from the PDF")
        else:
            self.set_note("The copy also drops all metadata")

        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>z"),
                                           Gtk.CallbackAction.new(lambda *_: self.canvas.undo() or True)))
        self.add_controller(keys)
        self.connect("close-request", lambda _w: self.pages.close() or False)
        self._on_change()

    def _on_change(self) -> None:
        n = len(self.canvas.boxes)
        self.count.set_text("Drag over what to hide" if n == 0 else
                            f"{n} box{'es' if n != 1 else ''} · click a box to remove it")
        self.primary_button.set_sensitive(n > 0)
        self.undo_button.set_sensitive(n > 0)

    def reset(self) -> None:
        self.canvas.boxes.clear()
        self.canvas.queue_draw()
        self._on_change()

    def apply(self) -> None:
        boxes = list(self.canvas.boxes)
        if not boxes:
            return
        if self.pages.is_pdf:
            def work(path, _progress):
                return pdf.redact(path, boxes)
        else:
            rects = [pixel_rect(b) for b in boxes]

            def work(path, _progress):
                return imageops.redact(path, rects)
        self.app.run_tool([self.src], "redact", work)
        self.close()
