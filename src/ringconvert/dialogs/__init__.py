# SPDX-License-Identifier: GPL-3.0-or-later
"""Tool dialogs: small smoked-glass floating panels, styled like the ring.

`ToolDialog` gives the shared frame: a draggable header with a round close
button at the top left and a centred title, a body, and a footer with Reset
on the left and a bright-glass primary button on the right (colours: style.css).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import cairo
import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID  # noqa: E402


class ToolDialog(Adw.Window):
    def __init__(self, app, title: str, primary: str, width: int, reset: str | None = "Reset"):
        super().__init__(application=app, title=title, resizable=False)
        self.app = app
        self.add_css_class("ringconvert-dialog")
        self.set_default_size(width, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("dialog-panel")
        self.set_content(box)

        header = Gtk.CenterBox()
        header.add_css_class("dialog-header")
        close = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        close.add_css_class("dialog-close")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda _b: self.close())
        header.set_start_widget(close)
        label = Gtk.Label(label=title)
        label.add_css_class("dialog-title")
        header.set_center_widget(label)
        # A same-sized invisible twin keeps the title optically centred.
        spacer = Gtk.Box()
        spacer.set_size_request(26, 1)
        header.set_end_widget(spacer)
        handle = Gtk.WindowHandle(child=header)
        box.append(handle)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.body.add_css_class("dialog-body")
        box.append(self.body)

        footer = Gtk.Box(spacing=10)
        footer.add_css_class("dialog-footer")
        self.reset_button = Gtk.Button(label=reset or "Reset", visible=reset is not None)
        self.reset_button.add_css_class("dialog-secondary")
        self.reset_button.connect("clicked", lambda _b: self.reset())
        footer.append(self.reset_button)
        # A short hint between the buttons (e.g. what Redact really removes);
        # always present, even empty, so it keeps the primary button on the right.
        self.footer_note = Gtk.Label(hexpand=True, xalign=0.5, wrap=True,
                                     justify=Gtk.Justification.CENTER)
        self.footer_note.add_css_class("dialog-note")
        footer.append(self.footer_note)
        self.primary_button = Gtk.Button(label=primary)
        self.primary_button.add_css_class("dialog-primary")
        self.primary_button.connect("clicked", lambda _b: self.apply())
        footer.append(self.primary_button)
        box.append(footer)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.set_default_widget(self.primary_button)

    def set_note(self, text: str) -> None:
        self.footer_note.set_text(text)

    def _on_key(self, _ctrl, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def reset(self) -> None:
        raise NotImplementedError

    def apply(self) -> None:
        raise NotImplementedError


def icon_button(icon: str, tooltip: str, on_click, toggle: bool = False) -> Gtk.Button:
    """A small flat icon button in the dialog style."""
    button = (Gtk.ToggleButton if toggle else Gtk.Button)(icon_name=icon, tooltip_text=tooltip)
    button.add_css_class("dialog-icon")
    button.connect("toggled" if toggle else "clicked", on_click)
    return button


_color_css: Gtk.CssProvider | None = None
_color_classes: dict[str, str] = {}


def swatch_class(color: str) -> str:
    """A CSS class that paints a swatch `color` (e.g. "#e8462a")."""
    global _color_css
    color = color.lower()
    if color not in _color_classes:
        _color_classes[color] = f"swatch-c{len(_color_classes)}"
        if _color_css is None:
            _color_css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), _color_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _color_css.load_from_string("\n".join(
            f".{cls} {{ background-color: {c}; }}" for c, cls in _color_classes.items()))
    return _color_classes[color]


def row(label: str, widget: Gtk.Widget, *extra: Gtk.Widget) -> Gtk.Box:
    """A control row: a fixed-width caption on the left, then the widgets."""
    box = Gtk.Box(spacing=10)
    box.add_css_class("dialog-row")
    caption = Gtk.Label(label=label, xalign=0)
    caption.add_css_class("dialog-caption")
    caption.set_size_request(86, -1)
    box.append(caption)
    box.append(widget)
    for w in extra:
        box.append(w)
    return box


class Segmented(Gtk.Box):
    """macOS-style segmented control: linked toggle buttons, one active."""

    def __init__(self, options: list[str], on_change: Callable[[str], None]):
        super().__init__()
        self.add_css_class("segmented")
        self._buttons: dict[str, Gtk.ToggleButton] = {}
        self._on_change = on_change
        self._quiet = False
        group = None
        for option in options:
            button = Gtk.ToggleButton(label=option)
            if group:
                button.set_group(group)
            group = group or button
            button.connect("toggled", self._toggled, option)
            self.append(button)
            self._buttons[option] = button
        group.set_active(True)

    def _toggled(self, button: Gtk.ToggleButton, option: str) -> None:
        if button.get_active() and not self._quiet:
            self._on_change(option)

    @property
    def value(self) -> str:
        return next(o for o, b in self._buttons.items() if b.get_active())

    def set_value(self, option: str, notify: bool = False) -> None:
        self._quiet = not notify
        try:
            self._buttons[option].set_active(True)
        finally:
            self._quiet = False


# ---------------------------------------------------------------- images


def preview_copy(img, longest: int):
    """A downsized copy for on-screen previews (never upscaled)."""
    from PIL import Image

    scale = min(1.0, longest / max(img.size))
    if scale >= 1.0:
        return img.copy()
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def to_texture(img) -> Gdk.Texture:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = GLib.Bytes.new(img.tobytes())
    return Gdk.MemoryTexture.new(img.width, img.height, Gdk.MemoryFormat.R8G8B8A8, data, img.width * 4)


def to_surface(img) -> cairo.ImageSurface:
    """An opaque Cairo surface; transparency is shown over a checkerboard."""
    from PIL import Image

    if img.mode == "RGBA":
        board = Image.new("RGB", img.size, (255, 255, 255))
        tile = 12
        dark = Image.new("RGB", (tile, tile), (222, 222, 222))
        for y in range(0, img.height, tile):
            for x in range((y // tile) % 2 * tile, img.width, tile * 2):
                board.paste(dark, (x, y))
        board.paste(img, (0, 0), img)
        img = board
    elif img.mode != "RGB":
        img = img.convert("RGB")
    data = bytearray(img.tobytes("raw", "BGRX"))
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_RGB24, img.width, img.height,
                                              img.width * 4)


# ---------------------------------------------------------------- opening


# tool -> (module, class); imported lazily so the ring starts fast.
DIALOGS = {
    "metadata": ("metadata", "MetadataDialog"),
    "edit": ("edit", "EditDialog"),
    "annotate": ("annotate", "AnnotateDialog"),
    "add_bg": ("add_bg", "AddBackgroundDialog"),
    "crop": ("crop", "CropDialog"),
    "redact": ("redact", "RedactDialog"),
}


def open_dialog(app, tool: str, paths: list[Path]) -> Gtk.Window | None:
    import importlib

    module, name = DIALOGS[tool]
    try:
        cls = getattr(importlib.import_module(f".{module}", __name__), name)
        dialog = cls(app, [Path(p) for p in paths])
    except Exception as exc:  # unreadable image, missing backend, ...
        print(f"ringconvert: {tool}: {exc}", file=sys.stderr, flush=True)
        note = Gio.Notification.new(f"Couldn't open {tool.replace('_', ' ')}")
        note.set_body(str(exc))
        note.set_icon(Gio.ThemedIcon.new(APP_ID))
        app.send_notification(f"dialog-{tool}", note)
        return None
    dialog.present()
    return dialog
