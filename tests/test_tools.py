# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
from PIL import Image

from conftest import needs
from ringconvert import cli
from ringconvert.core import imageops, metadata
from ringconvert.core.compress import compress
from ringconvert.core.detect import detect


def test_crop_box(jpg):
    [out] = imageops.crop(jpg, (100, 50, 300, 200)).outputs
    assert out.name == "Photo Cropped.jpg"
    assert Image.open(out).size == (300, 200)


@pytest.mark.parametrize("ratio, size", [("1:1", (480, 480)), ("16:9", (640, 360)), ("9:16", (270, 480))])
def test_crop_ratio(jpg, ratio, size):
    [out] = imageops.crop(jpg, ratio=ratio).outputs
    assert Image.open(out).size == size


def test_crop_clamped_to_image(jpg):
    [out] = imageops.crop(jpg, (600, 400, 500, 500)).outputs
    assert Image.open(out).size == (40, 80)


def test_edit(jpg):
    params = imageops.EditParams(rotate=90, flip_h=True, width=240, brightness=1.2, saturation=0)
    [out] = imageops.edit(jpg, params).outputs
    img = Image.open(out)
    assert out.name == "Photo Edited.jpg"
    assert img.size == (240, 320)  # rotated to 480x640, then scaled to 240 wide
    r, g, b = img.convert("RGB").getpixel((120, 160))
    assert abs(r - g) < 12 and abs(g - b) < 12  # desaturated


def test_add_background(jpg, tmp_path):
    cropped = imageops.crop(jpg, (0, 0, 400, 300)).outputs[0]
    bg = imageops.Background(kind="gradient", colors=["#ff0000", "#0000ff"], angle=90)
    [out] = imageops.add_background(cropped, bg, padding=100, corners=40, shadow=20, ratio="16:9").outputs
    assert out.name == "Photo Cropped With Background.png"
    img = Image.open(out).convert("RGB")
    assert img.size == (889, 500)  # 600x500 padded, widened to 16:9
    left, right = img.getpixel((3, 250)), img.getpixel((img.width - 4, 250))
    assert left[0] > 200 and right[2] > 200  # red -> blue left to right
    # The photo starts at (244, 100): its rounded corner shows background.
    assert img.getpixel((246, 102)) != imageops.load(cropped).getpixel((2, 2))
    assert img.getpixel((444, 250)) == imageops.load(cropped).convert("RGB").getpixel((200, 150))


def test_add_background_auto_ratio_solid(png_rgba):
    bg = imageops.Background(kind="solid", colors=["#2f80ed"])
    [out] = imageops.add_background(png_rgba, bg, padding=50, shadow=0).outputs
    img = Image.open(out).convert("RGB")
    assert img.size == (400, 300)
    assert img.getpixel((5, 5)) == (47, 128, 237)


def test_add_background_image(jpg, png_rgba):
    bg = imageops.Background(kind="image", path=jpg)
    [out] = imageops.add_background(png_rgba, bg, padding=20).outputs
    assert Image.open(out).size == (340, 240)


def test_annotate(jpg):
    shapes = [
        {"type": "rect", "points": [[10, 10], [200, 150]], "color": "#00ff00", "width": 8},
        {"type": "arrow", "points": [[300, 400], [500, 250]], "color": "#0000ff", "width": 6},
        {"type": "highlight", "points": [[0, 440], [640, 470]], "color": "#ffff00"},
        {"type": "pen", "points": [[20, 300], [60, 320], [100, 300]], "width": 5},
        {"type": "text", "points": [[20, 200]], "text": "Hello", "size": 30, "color": "#000000"},
    ]
    [out] = imageops.annotate(jpg, shapes).outputs
    img = Image.open(out).convert("RGB")
    assert out.name == "Photo Annotated.jpg"
    r, g, b = img.getpixel((12, 80))
    assert g > 200 and r < 80 and b < 80  # rectangle outline


def test_redact_image_drops_exif(jpg):
    [out] = imageops.redact(jpg, [(120, 80, 300, 300)]).outputs
    img = Image.open(out)
    assert out.name == "Photo Redacted.jpg"
    assert max(img.convert("RGB").getpixel((270, 230))) < 10
    assert not img.getexif()


def test_compress_images(jpg, png_rgba):
    [out] = compress(detect(jpg)).outputs
    assert out.name == "Photo Compressed.jpg"
    assert not Image.open(out).getexif()
    [out] = compress(detect(png_rgba)).outputs
    assert Image.open(out).mode in ("P", "RGBA")


def test_image_metadata_read_and_strip(jpg):
    info = detect(jpg)
    tags = metadata.read(info)
    assert any(v == "RingConvert Test Camera" for _, _, v in tags)
    [out] = metadata.strip(info).outputs
    assert out.name == "Photo Clean.jpg"
    assert not any(v == "RingConvert Test Camera" for _, _, v in metadata.read(detect(out)))


@needs("docx")
def test_docx_metadata_strip(docx_file):
    import docx

    [out] = metadata.strip(detect(docx_file)).outputs
    assert docx.Document(out).core_properties.author == ""


# ---------------------------------------------------------------- CLI


def test_cli_convert_and_tools(jpg, tmp_path, capsys):
    assert cli.main([str(jpg), "--to", "png", "-q"]) == 0
    assert capsys.readouterr().out.strip() == str(jpg.with_suffix(".png"))
    assert cli.main([str(jpg), "--tool", "crop", "--ratio", "1:1", "-q"]) == 0
    assert cli.main([str(jpg), "--tool", "add-bg", "--background", "gradient:#f60,#90f@45", "-q"]) == 0
    assert cli.main([str(jpg), "--tool", "edit", "--rotate", "180", "--resize", "x100", "-q"]) == 0
    assert cli.main([str(jpg), "--tool", "redact", "--rect", "0,0,50,50", "-q"]) == 0
    shapes = tmp_path / "shapes.json"
    shapes.write_text(json.dumps([{"type": "rect", "points": [[1, 1], [30, 30]]}]))
    assert cli.main([str(jpg), "--tool", "annotate", "--shapes", str(shapes), "-q"]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    for expected in ("Photo.png", "Photo Cropped.jpg", "Photo With Background.png",
                     "Photo Edited.jpg", "Photo Redacted.jpg", "Photo Annotated.jpg"):
        assert expected in names
    assert Image.open(tmp_path / "Photo Edited.jpg").size == (133, 100)


def test_cli_list_and_errors(jpg, docx_file, capsys):
    assert cli.main([str(jpg), "--list"]) == 0
    out = capsys.readouterr().out
    assert "convert: PNG WEBP HEIC GIF AVIF BMP TIFF PDF DOCX" in out
    assert "tools:   COMPRESS METADATA EDIT ANNOTATE ADD BG CROP REDACT" in out
    assert cli.main([str(jpg), "--to", "jpg"]) == 1
    assert "already JPG" in capsys.readouterr().err
    assert cli.main([str(docx_file), "--tool", "crop", "--box", "0,0,1,1"]) == 1
    assert cli.main(["/nonexistent.pdf", "--to", "jpg"]) == 1


def test_cli_pdf_redact(pdf1):
    assert cli.main([str(pdf1), "--tool", "redact", "--rect", "1:60,120,300,30", "-q"]) == 0
    assert (pdf1.parent / "Page 001 Redacted.pdf").exists()
