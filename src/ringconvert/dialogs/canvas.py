# SPDX-License-Identifier: GPL-3.0-or-later
"""A page you can draw on: an image, or a PDF rendered page by page.

`Pages` loads the document; `DocCanvas` shows one page fitted into a fixed
area and reports pointer drags in document units (image pixels, or PDF
points with the origin at the top left), which is what the core tools take.
"""

from __future__ import annotations

from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core import imageops, pdf  # noqa: E402
from ..core.detect import detect  # noqa: E402
from . import icon_button, preview_copy, to_surface  # noqa: E402


class Pages:
    def __init__(self, path: Path, preview_px: int = 1100):
        self.path = path
        self.is_pdf = detect(path).kind == "pdf"
        self.preview_px = preview_px
        self._surfaces: dict[int, cairo.ImageSurface] = {}
        if self.is_pdf:
            self._doc = pdf.open_pdf(path)
            self.count = self._doc.page_count
            self._sizes = [(p.rect.width, p.rect.height) for p in self._doc]
        else:
            self._doc = None
            self.image = imageops.load(path)
            self.count = 1
            self._sizes = [self.image.size]

    def size(self, index: int) -> tuple[float, float]:
        return self._sizes[index]

    @property
    def longest(self) -> float:
        """The longest page side in document units (for default stroke sizes)."""
        return max(max(w, h) for w, h in self._sizes)

    def surface(self, index: int) -> cairo.ImageSurface:
        """The page rendered at about `preview_px` on its long side."""
        if index not in self._surfaces:
            if self.is_pdf:
                w, h = self._sizes[index]
                dpi = round(max(36, min(300, 72 * self.preview_px / max(w, h))))  # PyMuPDF wants an int
                img = pdf.render_page(self._doc, index, dpi=dpi)
            else:
                img = preview_copy(self.image, self.preview_px)
            self._surfaces[index] = to_surface(img)
        return self._surfaces[index]

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None


class DocCanvas(Gtk.DrawingArea):
    """Shows `pages[page]` centred in a fixed `width` x `height` area.

    Subclass hooks, all in document units: `draw_overlay(cr)` (the context is
    already transformed so 1 unit = 1 document unit), `press(x, y)`,
    `drag(x, y)`, `release(x, y)`.
    """

    def __init__(self, pages: Pages, width: int, height: int):
        super().__init__()
        self.pages = pages
        self.page = 0
        self.page_listeners: list = []  # called with the new page index
        self.set_content_width(width)
        self.set_content_height(height)
        self.set_draw_func(self._draw)
        self._start = (0.0, 0.0)
        gesture = Gtk.GestureDrag()
        gesture.connect("drag-begin", self._on_begin)
        gesture.connect("drag-update", self._on_update)
        gesture.connect("drag-end", self._on_end)
        self.add_controller(gesture)

    # ------------------------------------------------------------ mapping

    def _area(self) -> tuple[int, int]:
        # Before the first allocation, fall back to the requested size.
        return (self.get_width() or self.get_content_width(),
                self.get_height() or self.get_content_height())

    def scale(self) -> float:
        w, h = self.pages.size(self.page)
        aw, ah = self._area()
        return min(aw / w, ah / h)

    def offset(self) -> tuple[float, float]:
        w, h = self.pages.size(self.page)
        s = self.scale()
        aw, ah = self._area()
        return (aw - w * s) / 2, (ah - h * s) / 2

    def to_doc(self, x: float, y: float, clamp: bool = True) -> tuple[float, float]:
        s = self.scale()
        ox, oy = self.offset()
        dx, dy = (x - ox) / s, (y - oy) / s
        if clamp:
            w, h = self.pages.size(self.page)
            dx, dy = min(max(dx, 0.0), w), min(max(dy, 0.0), h)
        return dx, dy

    def inside(self, x: float, y: float) -> bool:
        w, h = self.pages.size(self.page)
        dx, dy = self.to_doc(x, y, clamp=False)
        return 0 <= dx <= w and 0 <= dy <= h

    def set_page(self, page: int) -> None:
        self.page = page
        self.queue_draw()
        for listener in self.page_listeners:
            listener(page)

    # ------------------------------------------------------------ input

    def _on_begin(self, _gesture, x, y) -> None:
        self._start = (x, y)
        if self.inside(x, y):
            self.press(*self.to_doc(x, y))
        else:
            self._start = None

    def _on_update(self, _gesture, dx, dy) -> None:
        if self._start is not None:
            self.drag(*self.to_doc(self._start[0] + dx, self._start[1] + dy))

    def _on_end(self, _gesture, dx, dy) -> None:
        if self._start is not None:
            self.release(*self.to_doc(self._start[0] + dx, self._start[1] + dy))
        self._start = None

    def press(self, x: float, y: float) -> None:
        pass

    def drag(self, x: float, y: float) -> None:
        pass

    def release(self, x: float, y: float) -> None:
        pass

    def draw_overlay(self, cr: cairo.Context) -> None:
        pass

    # ------------------------------------------------------------ drawing

    def _draw(self, _area, cr: cairo.Context, _width: int, _height: int) -> None:
        s = self.scale()
        ox, oy = self.offset()
        w, h = self.pages.size(self.page)
        surface = self.pages.surface(self.page)
        cr.save()
        cr.translate(ox, oy)
        # A soft shadow so a white page stands out from the panel.
        cr.set_source_rgba(0.3, 0.12, 0.04, 0.18)
        cr.rectangle(1, 2, w * s, h * s)
        cr.fill()
        k = w * s / surface.get_width()
        cr.scale(k, k)
        cr.set_source_surface(surface, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

        cr.save()
        cr.translate(ox, oy)
        cr.scale(s, s)
        cr.rectangle(0, 0, w, h)
        cr.clip()
        self.draw_overlay(cr)
        cr.restore()


class PageNav(Gtk.Box):
    """"‹ Page 1 of 3 ›" under a canvas; hidden for single pages."""

    def __init__(self, canvas: DocCanvas):
        super().__init__(spacing=6, halign=Gtk.Align.CENTER)
        self.add_css_class("page-nav")
        self.canvas = canvas
        self.prev = icon_button("go-previous-symbolic", "Previous page", lambda _b: self.go(-1))
        self.next = icon_button("go-next-symbolic", "Next page", lambda _b: self.go(1))
        self.label = Gtk.Label()
        self.label.add_css_class("dialog-caption")
        for w in (self.prev, self.label, self.next):
            self.append(w)
        self.set_visible(canvas.pages.count > 1)
        canvas.page_listeners.append(lambda _page: self._update())
        self._update()

    def go(self, step: int) -> None:
        self.canvas.set_page(min(max(self.canvas.page + step, 0), self.canvas.pages.count - 1))

    def _update(self) -> None:
        count = self.canvas.pages.count
        self.label.set_text(f"Page {self.canvas.page + 1} of {count}")
        self.prev.set_sensitive(self.canvas.page > 0)
        self.next.set_sensitive(self.canvas.page < count - 1)
