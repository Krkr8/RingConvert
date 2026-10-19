# SPDX-License-Identifier: GPL-3.0-or-later
"""The Files right-click menu (nautilus/ringconvert-nautilus.py), outside Files:
fake Nautilus.FileInfo objects in, the Nautilus.MenuItem tree out; activating
an item calls a private-ID app instance (test_service's fixture) for real."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

Gio = pytest.importorskip("gi.repository.Gio")
gi = pytest.importorskip("gi")
try:
    gi.require_version("Nautilus", "4.1")
except ValueError:
    pytest.skip("needs the Nautilus 4.1 typelib (nautilus-extensions)", allow_module_level=True)

from ringconvert.core.detect import ALL_TOOLS, common_targets, detect, targets  # noqa: E402
from test_service import service  # noqa: E402,F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("ringconvert_nautilus", ROOT / "nautilus/ringconvert-nautilus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ext = _load()


class FakeFile:
    """The bits of Nautilus.FileInfo the extension uses."""

    def __init__(self, target, mime=None, directory=False):
        self.uri = target if "://" in str(target) else Gio.File.new_for_path(str(target)).get_uri()
        self.mime = mime
        self.directory = directory

    def get_uri(self):
        return self.uri

    def get_location(self):
        return Gio.File.new_for_uri(self.uri)

    def get_mime_type(self):
        return self.mime

    def is_directory(self):
        return self.directory


def tree(items):
    """[(label, sensitive, children)] of a menu item list."""
    out = []
    for item in items:
        menu = item.props.menu
        out.append((item.props.label, item.props.sensitive, tree(menu.get_items()) if menu else None))
    return out


def menu(files):
    return tree(ext.RingConvertMenu().get_file_items(files))


def test_single_image(jpg):
    [(title, _, children)] = menu([FakeFile(jpg, "image/jpeg")])
    assert title == "Convert with RingConvert"
    labels = [c[0] for c in children]
    assert labels[:-1] == [t.upper() for t in targets(detect(jpg))]
    assert "JPG" not in labels
    tools_label, _, tools = children[-1]
    assert tools_label == "Tools"
    assert [t[0] for t in tools] == [ext.TOOL_LABELS[t] for t in ALL_TOOLS]
    assert all(sensitive for _, sensitive, _ in tools)


def test_batch_has_common_formats_and_no_single_file_tools(jpg, png_rgba):
    files = [FakeFile(jpg, "image/jpeg"), FakeFile(png_rgba, "image/png")]
    formats, tools = ext.menu_entries(files)
    assert formats == common_targets([detect(jpg), detect(png_rgba)])
    assert "jpg" not in formats and "png" not in formats
    assert dict(tools) == {"compress": True, "metadata": True, "edit": False, "annotate": False,
                           "add_bg": True, "crop": False, "redact": False}


def test_uses_the_mime_type_from_files_without_reading(tmp_path):
    # A name Files already identified: no content sniffing needed (or possible).
    ghost = tmp_path / "missing.pdf"
    formats, tools = ext.menu_entries([FakeFile(ghost, "application/pdf")])
    assert formats == targets(detect(ghost, "application/pdf"))
    assert "redact" in dict(tools)


@pytest.mark.parametrize("make", [
    lambda d: FakeFile(d, "inode/directory", directory=True),
    lambda d: FakeFile("sftp://host/photo.jpg", "image/jpeg"),
    lambda d: FakeFile(d / "notes.xyz", "application/octet-stream"),
])
def test_no_menu_for_folders_remote_or_unknown_files(tmp_path, make):
    assert menu([make(tmp_path)]) == []


def test_one_unsupported_file_hides_the_menu(jpg, tmp_path):
    assert menu([FakeFile(jpg, "image/jpeg"), FakeFile(tmp_path, directory=True)]) == []


def _activate(items, *path):
    for label in path:
        [item] = [i for i in items if i.props.label == label]
        menu = item.props.menu
        items = menu.get_items() if menu else None
    item.activate()


def _wait_for(path: Path, timeout=20):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} wasn't written"
        time.sleep(0.1)


def test_menu_converts_through_the_app(service, jpg, png_rgba, monkeypatch):
    monkeypatch.setattr(ext, "APP_ID", service.app_id)
    monkeypatch.setattr(ext, "OBJECT_PATH", "/" + service.app_id.replace(".", "/"))
    items = ext.RingConvertMenu().get_file_items([FakeFile(jpg, "image/jpeg"), FakeFile(png_rgba, "image/png")])
    _activate(items, "Convert with RingConvert", "WEBP")
    _wait_for(jpg.with_suffix(".webp"))
    _wait_for(png_rgba.with_suffix(".webp"))
    _activate(items, "Convert with RingConvert", "Tools", "Compress")
    _wait_for(jpg.with_name("Photo Compressed.jpg"))


def test_dbus_convert_and_tool_errors(service, jpg, png_rgba):
    uris = [Gio.File.new_for_path(str(p)).get_uri() for p in (jpg, png_rgba)]
    with pytest.raises(Exception, match="one file at a time"):
        service.call("RunTool", uris, "crop", signature="(ass)")
    with pytest.raises(Exception, match="unknown tool"):
        service.call("RunTool", uris[:1], "nope", signature="(ass)")
    with pytest.raises(Exception, match="not a local file"):
        service.call("Convert", ["sftp://host/x.jpg"], "png", signature="(ass)")
