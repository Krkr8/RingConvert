# SPDX-License-Identifier: GPL-3.0-or-later
"""Sample files generated on the fly, plus skip helpers for missing backends."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ringconvert.core import backends


def needs(*names: str):
    """Skip unless every program/module in `names` is installed."""
    missing = [n for n in names if not (backends.which(n) or backends.has_module(n))]
    return pytest.mark.skipif(bool(missing), reason=f"needs {', '.join(missing)}")


def ffmpeg_gen(dst: Path, *args: str) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(dst)], check=True)
    return dst


@pytest.fixture
def jpg(tmp_path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 480), "#f6d2b8")
    draw = ImageDraw.Draw(img)
    draw.ellipse((120, 80, 420, 380), fill="#e8462a")
    draw.rectangle((450, 300, 600, 450), fill="#3a2a22")
    exif = Image.Exif()
    exif[0x010F] = "RingConvert Test Camera"  # Make
    exif[0x0131] = "pytest"  # Software
    path = tmp_path / "Photo.jpg"
    img.save(path, "JPEG", quality=95, exif=exif.tobytes())
    return path


@pytest.fixture
def png_rgba(tmp_path) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((20, 20, 280, 180), radius=30, fill=(240, 90, 40, 255))
    path = tmp_path / "Logo.png"
    img.save(path)
    return path


@pytest.fixture
def gif_animated(tmp_path) -> Path:
    from PIL import Image, ImageDraw

    frames = []
    for i in range(4):
        frame = Image.new("RGB", (120, 80), "white")
        ImageDraw.Draw(frame).rectangle((i * 25, 20, i * 25 + 30, 60), fill="red")
        frames.append(frame)
    path = tmp_path / "Anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


def _make_pdf(path: Path, pages: int) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 90), f"Page {i + 1} of the sample", fontsize=20)
        page.insert_text((72, 140), "SECRET-NUMBER 4242", fontsize=14)
    doc.set_metadata({"author": "Test Author", "title": "Sample PDF"})
    doc.save(path)
    return path


@pytest.fixture
def pdf1(tmp_path) -> Path:
    return _make_pdf(tmp_path / "Page 001.pdf", 1)


@pytest.fixture
def pdf3(tmp_path) -> Path:
    return _make_pdf(tmp_path / "Report.pdf", 3)


@pytest.fixture
def docx_file(tmp_path) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Quarterly Notes", 1)
    document.add_paragraph("RingConvert turns this into other formats.")
    document.add_paragraph("Second paragraph with *stars*.")
    document.core_properties.author = "Test Author"
    path = tmp_path / "Notes.docx"
    document.save(path)
    return path


@pytest.fixture
def md_file(tmp_path) -> Path:
    path = tmp_path / "Readme.md"
    path.write_text("# Title\n\nSome **bold** text and a list:\n\n- one\n- two\n", encoding="utf-8")
    return path


@pytest.fixture
def html_file(tmp_path) -> Path:
    path = tmp_path / "Page.html"
    path.write_text("<!doctype html><html><body><h1>Hello</h1><p>World &amp; more</p></body></html>",
                    encoding="utf-8")
    return path


@pytest.fixture
def txt_file(tmp_path) -> Path:
    path = tmp_path / "Plain.txt"
    path.write_text("Line one <with> brackets\nLine two\n", encoding="utf-8")
    return path


@pytest.fixture
def csv_file(tmp_path) -> Path:
    path = tmp_path / "Table.csv"
    path.write_text("name,qty\napple,3\npear,5\n", encoding="utf-8")
    return path


@pytest.fixture
def wav_file(tmp_path) -> Path:
    if not backends.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    return ffmpeg_gen(tmp_path / "Tone.wav", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                      "-metadata", "artist=Test Artist")


@pytest.fixture
def mp4_file(tmp_path) -> Path:
    if not backends.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    from ringconvert.core.media import audio_args, h264_args

    return ffmpeg_gen(
        tmp_path / "Clip.mp4",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        *h264_args(23), *audio_args("m4a"), "-shortest", "-metadata", "title=Test Clip",
    )
