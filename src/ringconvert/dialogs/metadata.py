# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata dialog: list every tag (exiftool when available) and write a
stripped "Name Clean.ext" copy. With several files the list shows the first
one and stripping applies to all."""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..core import metadata  # noqa: E402
from ..core.detect import detect  # noqa: E402
from . import Segmented, ToolDialog  # noqa: E402

LIST_H = 330


def tag_rows(tags: list[metadata.Tag], show_all: bool, query: str) -> list[metadata.Tag]:
    """Tags to list: embedded only unless `show_all`, filtered by `query`."""
    rows = tags if show_all else metadata.embedded_only(tags)
    q = query.strip().lower()
    if q:
        rows = [t for t in rows if any(q in part.lower() for part in t)]
    return rows


class MetadataDialog(ToolDialog):
    def __init__(self, app, paths: list[Path]):
        super().__init__(app, "Metadata", "Strip All Metadata", width=540, reset=None)
        self.paths = paths
        self.info = detect(paths[0])
        self.tags: list[metadata.Tag] | None = None
        self.error: str | None = None

        well = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        well.add_css_class("preview-well")

        header = Gtk.Box(spacing=10)
        ctype = Gio.content_type_from_mime_type(self.info.mime) or self.info.mime
        icon = Gtk.Image(gicon=Gio.content_type_get_icon(ctype), pixel_size=32)
        header.append(icon)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=paths[0].name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("meta-file")
        names.append(title)
        more = f" · and {len(paths) - 1} more file{'s' if len(paths) > 2 else ''}" if len(paths) > 1 else ""
        self.subtitle = Gtk.Label(label=f"{GLib.format_size(paths[0].stat().st_size)}{more}", xalign=0)
        self.subtitle.add_css_class("dialog-caption")
        names.append(self.subtitle)
        header.append(names)
        well.append(header)

        filters = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text="Filter tags", hexpand=True)
        self.search.add_css_class("num-field")
        self.search.connect("search-changed", lambda _e: self._fill())
        filters.append(self.search)
        self.scope = Segmented(["Embedded", "All"], lambda _v: self._fill())
        filters.append(self.scope)
        well.append(filters)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("tag-list")
        self.status = Gtk.Label(label="Reading metadata…", wrap=True, justify=Gtk.Justification.CENTER)
        self.status.add_css_class("dialog-caption")
        self.list.set_placeholder(self.status)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=LIST_H,
                                      max_content_height=LIST_H, child=self.list)
        scroller.add_css_class("tag-scroller")
        well.append(scroller)
        self.body.append(well)

        if not metadata.can_strip(self.info):
            self.primary_button.set_sensitive(False)
            self.set_note(f"Stripping {(self.info.format or 'this file').upper()} files isn't supported yet")
        else:
            self.set_note("Saves a clean copy; the original is untouched")
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            tags, error = metadata.read(self.info), None
        except Exception as exc:
            tags, error = [], str(exc)
        GLib.idle_add(self._loaded, tags, error)

    def _loaded(self, tags, error) -> bool:
        self.tags, self.error = tags, error
        self._fill()
        return GLib.SOURCE_REMOVE

    def _fill(self) -> None:
        if self.tags is None:
            return
        self.list.remove_all()
        rows = tag_rows(self.tags, self.scope.value == "All", self.search.get_text())
        for group, name, value in rows:
            self.list.append(self._row(group, name, value))
        embedded = len(metadata.embedded_only(self.tags))
        if self.error:
            self.status.set_text(f"Couldn't read the metadata:\n{self.error}")
        elif not rows:
            self.status.set_text("No matching tags" if self.search.get_text().strip()
                                 else "No embedded metadata")
        size = GLib.format_size(self.paths[0].stat().st_size)
        more = f" · and {len(self.paths) - 1} more" if len(self.paths) > 1 else ""
        self.subtitle.set_text(f"{size} · {embedded} embedded tag{'s' if embedded != 1 else ''}{more}")

    def _row(self, group: str, name: str, value: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=10)
        box.add_css_class("tag-row")
        key = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        key.set_size_request(170, -1)
        name_label = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END, tooltip_text=name)
        name_label.add_css_class("tag-name")
        key.append(name_label)
        if group:
            group_label = Gtk.Label(label=group, xalign=0)
            group_label.add_css_class("tag-group")
            key.append(group_label)
        box.append(key)
        shown = value if len(value) <= 400 else value[:400] + "…"
        value_label = Gtk.Label(label=shown, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                                hexpand=True, max_width_chars=40)
        value_label.add_css_class("tag-value")
        box.append(value_label)
        return box

    def apply(self) -> None:
        def work(path, _progress):
            return metadata.strip(detect(path))

        self.app.run_tool(list(self.paths), "metadata", work)
        self.close()
