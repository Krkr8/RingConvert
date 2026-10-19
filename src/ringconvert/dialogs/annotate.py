# SPDX-License-Identifier: GPL-3.0-or-later
"""Annotate dialog: arrow, box, highlighter, freehand pen and text.

Shapes are core annotation dicts in document units (see core.imageops and
core.pdf.annotate): images get them drawn into the pixels, PDFs get real
annotations that stay editable in other viewers.
"""

from __future__ import annotations

import math
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core import imageops, pdf  # noqa: E402
from . import Segmented, ToolDialog, icon_button, row, swatch_class  # noqa: E402
from .canvas import DocCanvas, PageNav, Pages  # noqa: E402

TOOLS = {"Arrow": "arrow", "Box": "rect", "Highlight": "highlight", "Pen": "pen", "Text": "text"}
PALETTE = ["#e8462a", "#f2c94c", "#2e9d5b", "#2f6fed", "#1e1e1e", "#ffffff"]


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def text_size(width: float) -> float:
    """Text follows the stroke size slider."""
    return max(10.0, width * 7)


def draw_shape(cr: cairo.Context, shape: dict) -> None:
    """Cairo version of the core renderers, for the live canvas."""
    kind = shape["type"]
    r, g, b = hex_to_rgb(shape["color"])
    width = float(shape["width"])
    pts = shape["points"]
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind in ("rect", "highlight") and len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        if kind == "highlight":
            cr.set_source_rgba(r, g, b, 90 / 255)
            cr.fill()
        else:
            cr.set_source_rgb(r, g, b)
            cr.stroke()
    elif kind == "arrow" and len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        angle = math.atan2(y1 - y0, x1 - x0)
        head = max(12.0, width * 4)
        cr.set_source_rgb(r, g, b)
        cr.move_to(x0, y0)
        cr.line_to(x1 - math.cos(angle) * head * 0.8, y1 - math.sin(angle) * head * 0.8)
        cr.stroke()
        spread = math.radians(28)
        cr.move_to(x1, y1)
        cr.line_to(x1 - head * math.cos(angle - spread), y1 - head * math.sin(angle - spread))
        cr.line_to(x1 - head * math.cos(angle + spread), y1 - head * math.sin(angle + spread))
        cr.close_path()
        cr.fill()
    elif kind == "pen" and pts:
        cr.set_source_rgb(r, g, b)
        cr.move_to(*pts[0])
        for p in pts[1:] or pts:
            cr.line_to(*p)
        cr.stroke()
    elif kind == "text" and pts:
        size = float(shape.get("size", text_size(width)))
        cr.set_source_rgb(r, g, b)
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(size)
        x, y = pts[0]
        for i, line in enumerate(shape.get("text", "").splitlines()):
            cr.move_to(x, y + size * 0.95 + i * size * 1.2)
            cr.show_text(line)
        cr.new_path()


class AnnotateCanvas(DocCanvas):
    def __init__(self, pages: Pages, width: int, height: int, dialog: "AnnotateDialog"):
        super().__init__(pages, width, height)
        self.dialog = dialog
        self.shapes: list[dict] = []
        self.current: dict | None = None
        self.set_cursor_from_name("crosshair")

    def _new(self, x, y) -> dict:
        d = self.dialog
        shape = {"type": d.tool, "points": [[x, y]], "color": d.color, "width": d.width}
        if self.pages.is_pdf:
            shape["page"] = self.page
        return shape

    def press(self, x, y) -> None:
        if self.dialog.tool == "text":
            self.dialog.ask_text(x, y)
            return
        self.current = self._new(x, y)

    def drag(self, x, y) -> None:
        if not self.current:
            return
        pts = self.current["points"]
        if self.current["type"] == "pen":
            # Skip points closer than a screen pixel to keep strokes light.
            lx, ly = pts[-1]
            if math.hypot(x - lx, y - ly) * self.scale() >= 1.5:
                pts.append([x, y])
        else:
            self.current["points"] = [pts[0], [x, y]]
        self.queue_draw()

    def release(self, x, y) -> None:
        shape, self.current = self.current, None
        if not shape:
            return
        pts = shape["points"]
        if shape["type"] != "pen":
            pts = shape["points"] = [pts[0], [x, y]]
            (x0, y0), (x1, y1) = pts
            if math.hypot(x1 - x0, y1 - y0) * self.scale() < 4:
                self.queue_draw()
                return  # a stray click, not a shape
        self.add(shape)

    def add(self, shape: dict) -> None:
        self.shapes.append(shape)
        self.queue_draw()
        self.dialog.on_change()

    def undo(self) -> None:
        if self.shapes:
            self.shapes.pop()
            self.queue_draw()
            self.dialog.on_change()

    def draw_overlay(self, cr) -> None:
        for shape in self.shapes + ([self.current] if self.current else []):
            if shape.get("page", 0) == self.page:
                draw_shape(cr, shape)


class AnnotateDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        super().__init__(app, "Annotate", "Save Annotated", width=620, reset="Clear")
        self.src = paths[0]
        self.pages = Pages(self.src)
        self.tool = "arrow"
        self.color = PALETTE[0]

        well = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        well.add_css_class("preview-well")
        self.canvas = AnnotateCanvas(self.pages, 580, 460, self)
        self.canvas.set_halign(Gtk.Align.CENTER)
        well.append(self.canvas)
        well.append(PageNav(self.canvas))
        self.body.append(well)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls.add_css_class("dialog-controls")
        self.tools = Segmented(list(TOOLS), lambda label: setattr(self, "tool", TOOLS[label]))
        self.undo_button = icon_button("edit-undo-symbolic", "Undo (Ctrl+Z)", lambda _b: self.canvas.undo())
        self.tools.set_hexpand(False)
        spacer = Gtk.Box(hexpand=True)
        controls.append(row("Tool", self.tools, spacer, self.undo_button))
        controls.append(row("Colour", self._palette()))

        # Stroke width in document units: pixels for images, points for PDFs.
        longest = self.pages.longest
        if self.pages.is_pdf:
            low, high, default = 1, 12, 3
        else:
            low, high, default = 1, max(12, round(longest / 40)), max(2, round(longest / 300))
        self.default_width = default
        self.size_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, 1)
        self.size_scale.set_draw_value(False)
        self.size_scale.set_hexpand(True)
        self.size_scale.set_value(default)
        unit = "pt" if self.pages.is_pdf else "px"
        self.size_label = Gtk.Label(xalign=1, width_chars=8)
        self.size_label.add_css_class("slider-value")
        self.size_scale.connect("value-changed",
                                lambda s: self.size_label.set_text(f"{round(s.get_value())} {unit}"))
        self.size_label.set_text(f"{default} {unit}")
        controls.append(row("Size", self.size_scale, self.size_label))
        self.body.append(controls)

        if self.pages.is_pdf:
            self.set_note("Saved as real PDF annotations")

        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>z"),
                                           Gtk.CallbackAction.new(lambda *_: self.canvas.undo() or True)))
        self.add_controller(keys)
        self.connect("close-request", lambda _w: self.pages.close() or False)
        self.on_change()

    @property
    def width(self) -> int:
        return round(self.size_scale.get_value())

    # ------------------------------------------------------------ colours

    def _palette(self) -> Gtk.Box:
        box = Gtk.Box(spacing=7)
        self.color_buttons: list[Gtk.Button] = []
        for color in PALETTE:
            button = Gtk.Button(tooltip_text=color)
            button.add_css_class("swatch")
            button.add_css_class(swatch_class(color))
            button.connect("clicked", lambda b, c=color: self._pick(b, c))
            box.append(button)
            self.color_buttons.append(button)
        self.custom = Gtk.Button(tooltip_text="Custom colour…")
        self.custom.add_css_class("swatch")
        self.custom.add_css_class("swatch-color")
        self.custom.connect("clicked", self._choose_color)
        box.append(self.custom)
        self.color_buttons.append(self.custom)
        self.color_buttons[0].add_css_class("selected")
        return box

    def _pick(self, button: Gtk.Button, color: str) -> None:
        self.color = color
        for b in self.color_buttons:
            b.remove_css_class("selected")
        button.add_css_class("selected")

    def _choose_color(self, button: Gtk.Button) -> None:
        dialog = Gtk.ColorDialog(title="Annotation Colour", modal=True, with_alpha=False)

        def done(dlg, result):
            try:
                rgba = dlg.choose_rgba_finish(result)
            except GLib.Error:
                return
            color = "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))
            for cls in [c for c in button.get_css_classes() if c.startswith("swatch-c")]:
                button.remove_css_class(cls)
            button.add_css_class(swatch_class(color))
            button.add_css_class("picked")  # drops the rainbow; the colour class paints it
            self._pick(button, color)

        dialog.choose_rgba(self, None, None, done)

    # ------------------------------------------------------------ text

    def ask_text(self, x: float, y: float) -> None:
        """A small popover with an entry at the clicked point."""
        popover = Gtk.Popover(has_arrow=True)
        popover.add_css_class("annotate-text")
        entry = Gtk.Entry(placeholder_text="Text, then Enter", width_chars=22)
        entry.add_css_class("num-field")
        popover.set_child(entry)
        popover.set_parent(self.canvas)
        s = self.canvas.scale()
        ox, oy = self.canvas.offset()
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(ox + x * s), int(oy + y * s), 1, 1
        popover.set_pointing_to(rect)

        def commit(_entry) -> None:
            text = entry.get_text().strip()
            if text:
                shape = self.canvas._new(x, y)
                shape.update(type="text", text=text, size=text_size(self.width))
                self.canvas.add(shape)
            popover.popdown()

        entry.connect("activate", commit)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()
        entry.grab_focus()

    # ------------------------------------------------------------ actions

    def on_change(self) -> None:
        has = bool(self.canvas.shapes)
        self.primary_button.set_sensitive(has)
        self.undo_button.set_sensitive(has)

    def reset(self) -> None:
        self.canvas.shapes.clear()
        self.canvas.queue_draw()
        self.on_change()

    def apply(self) -> None:
        shapes = [dict(s) for s in self.canvas.shapes]
        if not shapes:
            return
        if self.pages.is_pdf:
            def work(path, _progress):
                return pdf.annotate(path, shapes)
        else:
            def work(path, _progress):
                return imageops.annotate(path, shapes)
        self.app.run_tool([self.src], "annotate", work)
        self.close()
