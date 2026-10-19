# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from ringconvert.core import naming
from ringconvert.core.detect import common_targets, detect, targets, tools


def test_jpg_ring_menus(jpg):
    info = detect(jpg)
    assert (info.kind, info.format) == ("image", "jpg")
    # The hub is a readout, so every format (TIFF too) gets a real segment.
    assert targets(info) == ["png", "webp", "heic", "gif", "avif", "bmp", "tiff", "pdf", "docx"]
    # Clockwise from the top.
    assert tools(info) == ["compress", "metadata", "edit", "annotate", "add_bg", "crop", "redact"]


def test_pdf_ring(pdf1):
    info = detect(pdf1)
    assert (info.kind, info.format) == ("pdf", "pdf")
    assert targets(info) == ["docx", "jpg", "png", "txt", "tiff"]
    assert "crop" not in tools(info)


@pytest.mark.parametrize("name, fmt", [
    ("a.png", "png"), ("a.webp", "webp"), ("a.tiff", "tiff"), ("a.md", "md"),
    ("a.html", "html"), ("a.txt", "txt"), ("a.csv", "csv"), ("a.mp4", "mp4"),
    ("a.mkv", "mkv"), ("a.flac", "flac"), ("a.docx", "docx"),
])
def test_never_offers_own_format(tmp_path, name, fmt):
    path = tmp_path / name
    path.write_bytes(b"")
    info = detect(path)
    assert info.format == fmt
    ring = targets(info)
    assert fmt not in ring
    assert 4 <= len(ring) <= 9


def test_docx_tools_have_no_crop(docx_file):
    info = detect(docx_file)
    assert info.kind == "document"
    assert "crop" not in tools(info)
    assert "pdf" in targets(info)


def test_batch_menu_is_intersection(jpg, png_rgba, pdf1):
    assert common_targets([detect(jpg), detect(png_rgba)]) == [
        "webp", "heic", "gif", "avif", "bmp", "tiff", "pdf", "docx"]
    assert common_targets([detect(jpg), detect(pdf1)]) == ["png", "tiff", "docx"]


def test_content_beats_extension(tmp_path, png_rgba):
    # A PNG saved with a .jpg name is still treated as PNG.
    fake = tmp_path / "lying.jpg"
    fake.write_bytes(png_rgba.read_bytes())
    assert detect(fake).format == "png"


def test_unique_names(tmp_path):
    src = tmp_path / "Page 001.pdf"
    src.touch()
    first = naming.output_path(src, "jpg")
    second = naming.output_path(src, "jpg")
    third = naming.output_path(src, ".jpg", " Cropped")
    fourth = naming.output_path(src, "jpg", " Cropped")
    assert [p.name for p in (first, second, third, fourth)] == [
        "Page 001.jpg", "Page 001 2.jpg", "Page 001 Cropped.jpg", "Page 001 Cropped 2.jpg"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_unwritable_folder_falls_back_to_downloads(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    src = locked / "Doc.pdf"
    src.touch()
    downloads = tmp_path / "Downloads"
    monkeypatch.setattr(naming, "downloads_dir", lambda: downloads)
    locked.chmod(0o555)
    try:
        result = naming.Result()
        out = naming.output_path(src, "jpg", result=result)
    finally:
        locked.chmod(0o755)
    assert out == downloads / "Doc.jpg"
    assert result.notes and "Downloads" in result.notes[0]
