# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit dialog: rotate/flip, resize (W/H with an aspect lock), brightness,
contrast and saturation, with a live preview. Output "Name Edited.ext"."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core import imageops  # noqa: E402
from ..core.imageops import EditParams  # noqa: E402
from . import ToolDialog, icon_button, preview_copy, row, to_texture  # noqa: E402
from .crop import SizeField  # noqa: E402

PREVIEW_W, PREVIEW_H = 468, 300
PREVIEW_SIDE = 560


def rotated_size(size: tuple[int, int], rotate: int) -> tuple[int, int]:
    w, h = size
    return (h, w) if rotate % 180 else (w, h)


def locked_other(value: int, base: tuple[int, int], from_width: bool) -> int:
    """The other side for `value` when the aspect ratio of `base` is locked."""
    w, h = base
    return max(1, round(value * h / w) if from_width else round(value * w / h))


def params_for(size: tuple[int, int], rotate: int, flip_h: bool, flip_v: bool,
               target: tuple[int, int], brightness: float, contrast: float,
               saturation: float) -> EditParams:
    """EditParams for the dialog state; no resize when `target` is the
    rotated original size."""
    base = rotated_size(size, rotate)
    width, height = (None, None) if tuple(target) == base else target
    return EditParams(rotate=rotate % 360, flip_h=flip_h, flip_v=flip_v, width=width, height=height,
                      brightness=brightness, contrast=contrast, saturation=saturation)


class EditDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        super().__init__(app, "Edit", "Apply", width=520)
        self.src = paths[0]
        img = imageops.load(self.src)
        self.size = img.size
        self.preview_img = preview_copy(img, PREVIEW_SIDE)
        self.preview_scale = self.preview_img.width / img.width
        self._pending = 0

        well = Gtk.Box()
        well.add_css_class("preview-well")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
        self.picture.set_size_request(PREVIEW_W, PREVIEW_H)
        well.append(self.picture)
        self.body.append(well)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls.add_css_class("dialog-controls")

        transform = Gtk.Box(spacing=6)
        transform.append(icon_button("object-rotate-left-symbolic", "Rotate left", lambda _b: self._rotate(-90)))
        transform.append(icon_button("object-rotate-right-symbolic", "Rotate right", lambda _b: self._rotate(90)))
        transform.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.flip_h = icon_button("object-flip-horizontal-symbolic", "Flip horizontally",
                                  lambda _b: self._queue_render(), toggle=True)
        self.flip_v = icon_button("object-flip-vertical-symbolic", "Flip vertically",
                                  lambda _b: self._queue_render(), toggle=True)
        transform.append(self.flip_h)
        transform.append(self.flip_v)
        controls.append(row("Transform", transform))

        self.rotate = 0
        self.w_field = SizeField(lambda v: self._on_size(v, True))
        self.h_field = SizeField(lambda v: self._on_size(v, False))
        self.lock = icon_button("changes-prevent-symbolic", "Keep proportions",
                                lambda b: b.set_icon_name("changes-prevent-symbolic" if b.get_active()
                                                          else "changes-allow-symbolic"), toggle=True)
        self.lock.set_active(True)
        sizes = Gtk.Box(spacing=8)
        for caption, field in (("W", self.w_field), ("H", self.h_field)):
            label = Gtk.Label(label=caption)
            label.add_css_class("dialog-caption")
            sizes.append(label)
            sizes.append(field)
        unit = Gtk.Label(label="px")
        unit.add_css_class("dialog-caption")
        sizes.append(unit)
        sizes.append(self.lock)
        controls.append(row("Size", sizes))
        self.target = rotated_size(self.size, 0)

        self.scales: dict[str, Gtk.Scale] = {}
        for key, caption in (("brightness", "Brightness"), ("contrast", "Contrast"),
                             ("saturation", "Saturation")):
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 200, 1)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_value(100)
            scale.add_mark(100, Gtk.PositionType.BOTTOM, None)
            value = Gtk.Label(xalign=1, width_chars=8, label="100%")
            value.add_css_class("slider-value")
            scale.connect("value-changed", self._on_scale, value)
            self.scales[key] = scale
            controls.append(row(caption, scale, value))
        self.body.append(controls)
        self._show_size()
        self._render()

    # ------------------------------------------------------------ state

    def params(self, preview: bool = False) -> EditParams:
        p = params_for(self.size, self.rotate, self.flip_h.get_active(), self.flip_v.get_active(),
                       self.target, *(self.scales[k].get_value() / 100
                                      for k in ("brightness", "contrast", "saturation")))
        if preview and (p.width or p.height):
            k = self.preview_scale
            p = replace(p, width=max(1, round(p.width * k)), height=max(1, round(p.height * k)))
        return p

    def _rotate(self, degrees: int) -> None:
        self.rotate = (self.rotate + degrees) % 360
        self.target = (self.target[1], self.target[0])
        self._show_size()
        self._queue_render()

    def _on_size(self, value: int | None, from_width: bool) -> None:
        if value is not None:
            w, h = self.target
            if self.lock.get_active():
                other = locked_other(value, rotated_size(self.size, self.rotate), from_width)
                self.target = (value, other) if from_width else (other, value)
            else:
                self.target = (value, h) if from_width else (w, value)
            self._queue_render()
        self._show_size()

    def _show_size(self) -> None:
        self.w_field.show_value(self.target[0])
        self.h_field.show_value(self.target[1])

    def _on_scale(self, scale: Gtk.Scale, label: Gtk.Label) -> None:
        label.set_text(f"{round(scale.get_value())}%")
        self._queue_render()

    # ------------------------------------------------------------ preview

    def _queue_render(self) -> None:
        if not self._pending:
            self._pending = GLib.timeout_add(30, self._render)

    def _render(self) -> bool:
        self._pending = 0
        try:
            out = imageops.render_edit(self.preview_img, self.params(preview=True))
        except Exception as exc:
            print(f"ringconvert: preview: {exc}", file=sys.stderr, flush=True)
            return GLib.SOURCE_REMOVE
        self.picture.set_paintable(to_texture(out))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ actions

    def reset(self) -> None:
        self.rotate = 0
        self.flip_h.set_active(False)
        self.flip_v.set_active(False)
        self.lock.set_active(True)
        self.target = rotated_size(self.size, 0)
        for scale in self.scales.values():
            scale.set_value(100)
        self._show_size()
        self._queue_render()

    def apply(self) -> None:
        params = self.params()
        self.app.run_tool([self.src], "edit", lambda path, _progress: imageops.edit(path, params))
        self.close()
