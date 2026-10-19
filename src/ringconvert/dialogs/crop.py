# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop Image dialog: orange crop box with corner and edge
handles over a darkened image, aspect-ratio presets, W/H fields in px."""

from __future__ import annotations

import math
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..core import imageops  # noqa: E402
from . import Segmented, ToolDialog, preview_copy, row, to_surface  # noqa: E402
from . import cropmath  # noqa: E402

RATIOS = ["Free", "1:1", "16:9", "9:16", "4:3", "3:4"]
ORANGE = (0.94, 0.33, 0.13)

CURSORS = {"move": "move", "n": "n-resize", "s": "s-resize", "e": "e-resize", "w": "w-resize",
           "nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize", "se": "se-resize"}


class CropView(Gtk.DrawingArea):
    """The image with the crop box; all box maths is in image pixels."""

    GRAB = 14  # px around a handle that still grabs it

    def __init__(self, img, max_w: int, max_h: int, on_change):
        super().__init__()
        self.width, self.height = img.size
        # Twice the on-screen size so it stays sharp on HiDPI screens.
        self.surface = to_surface(preview_copy(img, 2 * max(max_w, max_h)))
        scale = min(max_w / self.width, max_h / self.height)
        self.set_content_width(round(self.width * scale))
        self.set_content_height(round(self.height * scale))
        self.box = cropmath.full(self.width, self.height)
        self.ratio: float | None = None
        self.on_change = on_change
        self._handle: str | None = None
        self._start_box = self.box
        self.set_draw_func(self._draw)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", lambda *_: setattr(self, "_handle", None))
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    # ------------------------------------------------------------ mapping

    def _scale(self) -> float:
        return min(self.get_width() / self.width, self.get_height() / self.height) or 1.0

    def _offset(self) -> tuple[float, float]:
        s = self._scale()
        return (self.get_width() - self.width * s) / 2, (self.get_height() - self.height * s) / 2

    def _rect(self) -> tuple[float, float, float, float]:
        """The crop box in widget coordinates."""
        s = self._scale()
        ox, oy = self._offset()
        x, y, w, h = self.box
        return ox + x * s, oy + y * s, w * s, h * s

    def handle_at(self, px: float, py: float) -> str | None:
        x, y, w, h = self._rect()
        grab = self.GRAB
        cols = {"w": x, "": x + w / 2, "e": x + w}
        rows = {"n": y, "": y + h / 2, "s": y + h}
        best, best_d = None, grab
        for v, cy in rows.items():
            for hz, cx in cols.items():
                if not v and not hz:
                    continue
                d = math.hypot(px - cx, py - cy)
                if d <= best_d:
                    best, best_d = v + hz, d
        if best:
            return best
        # Anywhere along an edge grabs that edge.
        inside_x, inside_y = x - grab / 2 <= px <= x + w + grab / 2, y - grab / 2 <= py <= y + h + grab / 2
        if inside_y and abs(px - x) <= grab / 2:
            return "w"
        if inside_y and abs(px - (x + w)) <= grab / 2:
            return "e"
        if inside_x and abs(py - y) <= grab / 2:
            return "n"
        if inside_x and abs(py - (y + h)) <= grab / 2:
            return "s"
        if x < px < x + w and y < py < y + h:
            return "move"
        return None

    # ------------------------------------------------------------ input

    def _on_motion(self, _ctrl, x, y) -> None:
        if self._handle is None:
            handle = self.handle_at(x, y)
            self.set_cursor_from_name(CURSORS.get(handle, "default"))

    def _on_begin(self, _gesture, x, y) -> None:
        self._handle = self.handle_at(x, y)
        self._start_box = self.box

    def _on_update(self, _gesture, dx, dy) -> None:
        if not self._handle:
            return
        s = self._scale()
        self.set_box(cropmath.drag(self._start_box, self._handle, dx / s, dy / s,
                                   self.width, self.height, self.ratio))

    def set_box(self, box) -> None:
        self.box = box
        self.queue_draw()
        self.on_change(box)

    # ------------------------------------------------------------ drawing

    def _draw(self, _area, cr: cairo.Context, width: int, height: int) -> None:
        s = self._scale()
        ox, oy = self._offset()
        img_w, img_h = self.width * s, self.height * s
        cr.save()
        cr.translate(ox, oy)
        k = img_w / self.surface.get_width()
        cr.scale(k, k)
        cr.set_source_surface(self.surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

        x, y, w, h = self._rect()
        # Darken everything outside the crop box.
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(ox, oy, img_w, img_h)
        cr.rectangle(x, y, w, h)
        cr.set_source_rgba(0.12, 0.1, 0.1, 0.55)
        cr.fill()
        cr.set_fill_rule(cairo.FILL_RULE_WINDING)

        cr.set_source_rgb(*ORANGE)
        cr.set_line_width(1.5)
        cr.rectangle(x + 0.75, y + 0.75, w - 1.5, h - 1.5)
        cr.stroke()
        # Square corner handles, short bars on the edge midpoints.
        c = 9
        for cx, cy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
            cr.rectangle(cx - c / 2, cy - c / 2, c, c)
        bar, thick = min(18.0, w / 4), 4.0
        cr.rectangle(x + w / 2 - bar / 2, y - thick / 2, bar, thick)
        cr.rectangle(x + w / 2 - bar / 2, y + h - thick / 2, bar, thick)
        bar = min(18.0, h / 4)
        cr.rectangle(x - thick / 2, y + h / 2 - bar / 2, thick, bar)
        cr.rectangle(x + w - thick / 2, y + h / 2 - bar / 2, thick, bar)
        cr.fill()


class SizeField(Gtk.Entry):
    def __init__(self, on_set):
        super().__init__(width_chars=6, max_width_chars=6, max_length=6,
                         input_purpose=Gtk.InputPurpose.DIGITS)
        self.add_css_class("num-field")
        self._on_set = on_set
        self.connect("activate", lambda _e: self._commit())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: self._commit())
        self.add_controller(focus)

    def _commit(self) -> None:
        text = self.get_text().strip()
        if text.isdigit() and int(text) > 0:
            self._on_set(int(text))
        else:
            self._on_set(None)  # restore the current value

    def show_value(self, value: float) -> None:
        text = str(round(value))
        if self.get_text() != text:
            self.set_text(text)


class CropDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        super().__init__(app, "Crop Image", "Apply", width=460)
        self.src = paths[0]
        img = imageops.load(self.src)

        well = Gtk.Box(halign=Gtk.Align.FILL)
        well.add_css_class("preview-well")
        self.view = CropView(img, 400, 440, self._on_box)
        self.view.set_halign(Gtk.Align.CENTER)
        self.view.set_hexpand(True)
        well.append(self.view)
        self.body.append(well)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls.add_css_class("dialog-controls")
        self.ratio = Segmented(RATIOS, self._on_ratio)
        controls.append(row("Aspect ratio", self.ratio))
        self.w_field = SizeField(lambda v: self._on_size(v, None))
        self.h_field = SizeField(lambda v: self._on_size(None, v))
        sizes = Gtk.Box(spacing=8)
        sizes.add_css_class("dialog-row")
        for caption, field in (("W", self.w_field), ("H", self.h_field)):
            label = Gtk.Label(label=caption)
            label.add_css_class("dialog-caption")
            sizes.append(label)
            sizes.append(field)
        unit = Gtk.Label(label="px")
        unit.add_css_class("dialog-caption")
        sizes.append(unit)
        controls.append(sizes)
        self.body.append(controls)
        self._on_box(self.view.box)

    def _on_box(self, box) -> None:
        x, y, w, h = cropmath.pixels(box)
        self.w_field.show_value(w)
        self.h_field.show_value(h)

    def _on_ratio(self, option: str) -> None:
        view = self.view
        view.ratio = imageops.ratio_value(option)
        if view.ratio:
            view.set_box(cropmath.fit_ratio(view.box, view.ratio, view.width, view.height))
        else:
            view.queue_draw()

    def _on_size(self, w: int | None, h: int | None) -> None:
        view = self.view
        if w is None and h is None:
            self._on_box(view.box)
            return
        view.set_box(cropmath.resize(view.box, w, h, view.width, view.height, view.ratio))
        self._on_box(view.box)  # show the clamped value

    def reset(self) -> None:
        self.ratio.set_value("Free")
        self.view.ratio = None
        self.view.set_box(cropmath.full(self.view.width, self.view.height))

    def apply(self) -> None:
        box = cropmath.pixels(self.view.box)
        self.app.run_tool([self.src], "crop", lambda path, _progress: imageops.crop(path, box=box))
        self.close()
