# SPDX-License-Identifier: GPL-3.0-or-later
"""Add BG dialog: put the image on a decorative background with
padding, rounded corners and a shadow, like a screenshot beautifier."""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import imageops  # noqa: E402
from ..core.imageops import GRADIENTS, SOLIDS, Background  # noqa: E402
from . import Segmented, ToolDialog, preview_copy, row, to_texture  # noqa: E402

RATIOS = ["Auto", "1:1", "16:9", "4:5"]
PREVIEW_W, PREVIEW_H = 468, 300
PREVIEW_SIDE = 560  # px of the downsized image the live preview is built from

_swatch_css: Gtk.CssProvider | None = None
_custom_css: Gtk.CssProvider | None = None  # the picked custom colour


def _install_swatch_css() -> None:
    """One CSS class per preset swatch (.swatch-g0 …, .swatch-s0 …)."""
    global _swatch_css
    if _swatch_css is not None:
        return
    rules = []
    for i, colors in enumerate(GRADIENTS):
        stops = ", ".join(colors)
        rules.append(f".swatch-g{i} {{ background-image: linear-gradient(135deg, {stops}); }}")
    for i, color in enumerate(SOLIDS):
        rules.append(f".swatch-s{i} {{ background-color: {color}; }}")
    _swatch_css = Gtk.CssProvider()
    _swatch_css.load_from_string("\n".join(rules))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), _swatch_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


class AddBackgroundDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        primary = "Save with Background" if len(paths) == 1 else f"Save {len(paths)} with Background"
        super().__init__(app, "Add BG", primary, width=520)
        _install_swatch_css()
        self.paths = paths
        img = imageops.load(paths[0])
        self.size = img.size
        self.preview_img = preview_copy(img, PREVIEW_SIDE)
        self.preview_scale = self.preview_img.width / img.width
        self._pending = 0
        self._custom_rgba: Gdk.RGBA | None = None

        well = Gtk.Box()
        well.add_css_class("preview-well")
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
        self.picture.set_size_request(PREVIEW_W, PREVIEW_H)
        self.picture.add_css_class("bg-preview")
        well.append(self.picture)
        self.body.append(well)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        controls.add_css_class("dialog-controls")
        controls.append(self._swatches())

        longest, shortest = max(img.size), min(img.size)
        self.defaults = {
            "padding": round(longest * 0.12),
            "corners": round(shortest * 0.04),
            "shadow": max(8, round(longest * 0.02)),
        }
        limits = {
            "padding": max(200, round(longest * 0.6)),
            "corners": max(40, shortest // 4),
            "shadow": max(60, round(longest * 0.08)),
        }
        self.scales: dict[str, Gtk.Scale] = {}
        for key, caption in (("padding", "Padding"), ("corners", "Corners"), ("shadow", "Shadow")):
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, limits[key], 1)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_value(self.defaults[key])
            value = Gtk.Label(xalign=1, width_chars=8)
            value.add_css_class("slider-value")
            scale.connect("value-changed", self._on_scale, value)
            self._on_scale(scale, value, render=False)
            self.scales[key] = scale
            controls.append(row(caption, scale, value))

        self.ratio = Segmented(RATIOS, lambda _r: self._queue_render())
        self.ratio.set_halign(Gtk.Align.END)
        self.ratio.set_hexpand(True)
        controls.append(row("Ratio", self.ratio))
        self.body.append(controls)

        self._select(self.swatch_buttons[0], Background(kind="gradient", colors=list(GRADIENTS[0])))
        self._render()

    # ------------------------------------------------------------ swatches

    def _swatches(self) -> Gtk.Box:
        self.swatch_buttons: list[Gtk.Button] = []
        grid = Gtk.Grid(column_spacing=7, row_spacing=7, hexpand=True)
        for i, colors in enumerate(GRADIENTS):
            bg = Background(kind="gradient", colors=list(colors))
            grid.attach(self._swatch(f"swatch-g{i}", bg), i, 0, 1, 1)
        for i, color in enumerate(SOLIDS):
            bg = Background(kind="solid", colors=[color])
            grid.attach(self._swatch(f"swatch-s{i}", bg), i, 1, 1, 1)

        image = Gtk.Button(icon_name="image-x-generic-symbolic", tooltip_text="Custom image…")
        image.add_css_class("swatch")
        image.add_css_class("swatch-image")
        image.connect("clicked", self._choose_image)
        grid.attach(image, len(GRADIENTS), 0, 1, 1)
        self.swatch_buttons.append(image)

        self.color_button = Gtk.Button(tooltip_text="Custom colour…")
        self.color_button.add_css_class("swatch")
        self.color_button.add_css_class("swatch-color")
        self.color_button.connect("clicked", self._choose_color)
        grid.attach(self.color_button, len(SOLIDS), 1, 1, 1)
        self.swatch_buttons.append(self.color_button)

        box = row("Background", grid)
        box.set_valign(Gtk.Align.START)
        box.get_first_child().set_valign(Gtk.Align.START)
        return box

    def _swatch(self, css: str, bg: Background) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("swatch")
        button.add_css_class(css)
        button.connect("clicked", lambda b: self._select(b, bg))
        self.swatch_buttons.append(button)
        return button

    def _select(self, button: Gtk.Button, bg: Background) -> None:
        self.background = bg
        for b in self.swatch_buttons:
            b.remove_css_class("selected")
        button.add_css_class("selected")
        self._queue_render()

    def _choose_image(self, button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Background Image", modal=True)
        images = Gtk.FileFilter(name="Images")
        images.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if file and file.get_path():
                self._select(button, Background(kind="image", path=Path(file.get_path())))

        dialog.open(self, None, done)

    def _choose_color(self, button: Gtk.Button) -> None:
        dialog = Gtk.ColorDialog(title="Background Colour", modal=True, with_alpha=False)

        def done(dlg, result):
            try:
                rgba = dlg.choose_rgba_finish(result)
            except GLib.Error:
                return
            self._custom_rgba = rgba
            hexa = "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))
            self._set_custom_swatch(hexa)
            self._select(button, Background(kind="solid", colors=[hexa]))

        dialog.choose_rgba(self, self._custom_rgba, None, done)

    def _set_custom_swatch(self, hexa: str) -> None:
        global _custom_css
        if _custom_css is None:
            _custom_css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), _custom_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        _custom_css.load_from_string(f".swatch-color.custom {{ background-color: {hexa}; }}")
        self.color_button.add_css_class("custom")

    # ------------------------------------------------------------ preview

    def _on_scale(self, scale: Gtk.Scale, label: Gtk.Label, render: bool = True) -> None:
        label.set_text(f"{round(scale.get_value())} px")
        if render:
            self._queue_render()

    def _values(self) -> dict:
        return {key: round(scale.get_value()) for key, scale in self.scales.items()}

    def _queue_render(self) -> None:
        # Coalesce slider drags: render at most every ~30 ms.
        if not self._pending:
            self._pending = GLib.timeout_add(30, self._render)

    def _render(self) -> bool:
        self._pending = 0
        v = self._values()
        k = self.preview_scale
        try:
            out = imageops.render_background(
                self.preview_img, self.background, round(v["padding"] * k), round(v["corners"] * k),
                round(v["shadow"] * k), self.ratio.value.lower())
        except Exception as exc:  # e.g. an unreadable custom image
            print(f"ringconvert: preview: {exc}", file=sys.stderr, flush=True)
            return GLib.SOURCE_REMOVE
        self.picture.set_paintable(to_texture(out))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ actions

    def reset(self) -> None:
        for key, scale in self.scales.items():
            scale.set_value(self.defaults[key])
        self.ratio.set_value("Auto")
        self._select(self.swatch_buttons[0], Background(kind="gradient", colors=list(GRADIENTS[0])))

    def apply(self) -> None:
        bg, v, ratio = self.background, self._values(), self.ratio.value.lower()

        def work(path: Path, _progress):
            return imageops.add_background(path, bg, v["padding"], v["corners"], v["shadow"], ratio)

        self.app.run_tool(list(self.paths), "add_bg", work)
        self.close()
