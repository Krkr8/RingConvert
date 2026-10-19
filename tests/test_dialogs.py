# SPDX-License-Identifier: GPL-3.0-or-later
"""Tool dialogs end to end: build the real dialog, set it up the way a user
would, Apply, and check the file it writes. Needs a display (skips without)."""

from pathlib import Path

import pytest

Gtk = pytest.importorskip("gi.repository.Gtk")
if not Gtk.init_check():
    pytest.skip("no display", allow_module_level=True)
Adw = pytest.importorskip("gi.repository.Adw")
Adw.init()

from PIL import Image  # noqa: E402

from ringconvert.app import RingConvertApp  # noqa: E402
from ringconvert.core import backends  # noqa: E402
from ringconvert.dialogs.annotate import AnnotateDialog  # noqa: E402
from ringconvert.dialogs.edit import EditDialog, locked_other, params_for  # noqa: E402
from ringconvert.dialogs.metadata import MetadataDialog, tag_rows  # noqa: E402
from ringconvert.dialogs.redact import RedactDialog, box_at, pixel_rect  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")


class FakeApp(RingConvertApp):
    """Runs a dialog's work synchronously instead of in a job thread."""

    def __init__(self):
        super().__init__()
        self.results = []

    def run_tool(self, paths, tool, work):
        for path in paths:
            self.results.append((tool, work(Path(path), lambda _f: None)))


@pytest.fixture
def app():
    return FakeApp()


@pytest.fixture
def photo(tmp_path):
    img = Image.new("RGB", (400, 300), (200, 120, 60))
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    exif[0x8825] = {1: "N", 2: (33.0, 18.0, 45.0)}
    path = tmp_path / "Photo.jpg"
    img.save(path, quality=95, exif=exif)
    return path


@pytest.fixture
def report(tmp_path):
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page()
        page.insert_text((72, 90), f"Report page {n + 1}", fontsize=20)
    doc[1].insert_text((72, 300), "Account 4111-2222", fontsize=14)
    doc.set_metadata({"author": "Someone"})
    path = tmp_path / "Report.pdf"
    doc.save(path)
    return path


def output(app):
    tool, result = app.results[-1]
    assert len(result.outputs) == 1
    return result.outputs[0]


# ---------------------------------------------------------------- edit


def test_edit_size_helpers():
    assert locked_other(800, (1600, 1200), from_width=True) == 600
    assert locked_other(600, (1600, 1200), from_width=False) == 800
    p = params_for((400, 300), 90, False, False, (300, 400), 1, 1, 1)
    assert (p.width, p.height) == (None, None)  # rotated original size: no resize
    p = params_for((400, 300), 0, False, False, (200, 150), 1, 1, 1)
    assert (p.width, p.height) == (200, 150)


def test_edit_rotate_resize_and_brighten(app, photo):
    dialog = EditDialog(app, [photo])
    dialog._rotate(90)
    dialog.w_field.set_text("150")
    dialog._on_size(150, True)  # aspect locked: 150 x 200
    dialog.scales["brightness"].set_value(150)
    dialog.apply()
    out = output(app)
    assert out.name == "Photo Edited.jpg"
    img = Image.open(out)
    assert img.size == (150, 200)
    assert img.getpixel((75, 100))[0] > 230  # brighter than the 200 it started at


# ---------------------------------------------------------------- annotate


def test_annotate_image_draws_shapes(app, photo):
    dialog = AnnotateDialog(app, [photo])
    assert not dialog.primary_button.get_sensitive()  # nothing drawn yet
    dialog.tool, dialog.color = "rect", "#2f6fed"
    canvas = dialog.canvas
    canvas.press(50, 50)
    canvas.drag(250, 200)
    canvas.release(250, 200)
    assert dialog.primary_button.get_sensitive()
    dialog.apply()
    img = Image.open(output(app)).convert("RGB")
    r, g, b = img.getpixel((51, 120))  # on the left edge of the box
    assert b > 180 and r < 120


def test_annotate_pdf_adds_real_annotations(app, report):
    dialog = AnnotateDialog(app, [report])
    dialog.canvas.set_page(1)
    dialog.tool = "arrow"
    dialog.canvas.press(100, 100)
    dialog.canvas.release(300, 200)
    dialog.canvas.add({"type": "text", "points": [[72, 400]], "color": "#e8462a", "width": 3,
                       "text": "Check this", "size": 16, "page": 1})
    dialog.apply()
    with pymupdf.open(output(app)) as doc:
        assert list(doc[0].annots()) == []
        kinds = sorted(a.type[1] for a in doc[1].annots())
    assert kinds == ["FreeText", "Line"]


def test_annotate_undo_and_stray_clicks(app, photo):
    dialog = AnnotateDialog(app, [photo])
    dialog.canvas.press(10, 10)
    dialog.canvas.release(10.5, 10)  # a click, not a shape
    assert dialog.canvas.shapes == []
    dialog.tool = "pen"
    dialog.canvas.press(10, 10)
    dialog.canvas.drag(60, 60)
    dialog.canvas.release(60, 60)
    assert len(dialog.canvas.shapes) == 1
    dialog.canvas.undo()
    assert dialog.canvas.shapes == []


# ---------------------------------------------------------------- redact


def test_redact_helpers():
    assert pixel_rect((0, 10.6, 5.2, 3.0, 3.0)) == (10, 5, 4, 4)
    boxes = [(0, 0, 0, 10, 10), (1, 0, 0, 10, 10), (0, 5, 5, 10, 10)]
    assert box_at(boxes, 0, 7, 7) == 2
    assert box_at(boxes, 1, 7, 7) == 1
    assert box_at(boxes, 0, 50, 50) is None


def test_redact_pdf_removes_the_text(app, report):
    with pymupdf.open(report) as doc:
        hit = doc[1].search_for("4111-2222")[0]
    dialog = RedactDialog(app, [report])
    dialog.canvas.set_page(1)
    dialog.canvas.press(hit.x0 - 2, hit.y0 - 2)
    dialog.canvas.drag(hit.x1 + 2, hit.y1 + 2)
    dialog.canvas.release(hit.x1 + 2, hit.y1 + 2)
    dialog.apply()
    out = output(app)
    assert out.name == "Report Redacted.pdf"
    with pymupdf.open(out) as doc:
        assert "4111" not in doc[1].get_text()  # removed, not just covered
        assert "Report page 2" in doc[1].get_text()
        assert not doc.metadata.get("author")


def test_redact_image_blacks_out_and_strips_metadata(app, photo):
    dialog = RedactDialog(app, [photo])
    dialog.canvas.press(100.4, 100.4)
    dialog.canvas.release(200.6, 150.6)
    dialog.apply()
    img = Image.open(output(app))
    assert max(img.convert("RGB").getpixel((100, 100))) < 30
    assert max(img.convert("RGB").getpixel((200, 150))) < 30  # edges covered too
    assert not img.getexif()


def test_redact_click_removes_a_box(app, photo):
    dialog = RedactDialog(app, [photo])
    dialog.canvas.press(10, 10)
    dialog.canvas.release(80, 80)
    dialog.canvas.press(40, 40)
    dialog.canvas.release(40, 40)
    assert dialog.canvas.boxes == []
    assert not dialog.primary_button.get_sensitive()


# ---------------------------------------------------------------- metadata


def test_tag_rows_filters():
    tags = [("System", "FileSize", "1 kB"), ("IFD0", "Make", "Canon"), ("GPS", "GPSLatitude", "33")]
    assert tag_rows(tags, False, "") == tags[1:]
    assert tag_rows(tags, True, "") == tags
    assert tag_rows(tags, False, "gps") == [tags[2]]


@pytest.mark.skipif(not backends.which("exiftool"), reason="exiftool not installed")
def test_metadata_strip_removes_gps(app, photo):
    dialog = MetadataDialog(app, [photo])
    dialog.apply()
    out = output(app)
    assert out.name == "Photo Clean.jpg"
    exif = Image.open(out).getexif()
    assert 0x8825 not in exif and 0x010F not in exif


def test_metadata_unsupported_format_disables_strip(app, tmp_path):
    path = tmp_path / "Notes.odt"  # only DOCX office files can be stripped
    path.write_bytes(b"PK\x03\x04 not really a document")
    dialog = MetadataDialog(app, [path])
    assert not dialog.primary_button.get_sensitive()


# ---------------------------------------------------------------- ring


def test_compress_runs_from_the_ring(photo):
    from ringconvert.ring_window import DIRECT_TOOLS

    result = DIRECT_TOOLS["compress"](photo, lambda _f: None)
    assert result.outputs[0].name == "Photo Compressed.jpg"
