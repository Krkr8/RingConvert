# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from PIL import Image

from conftest import needs
from ringconvert.core import backends, convert
from ringconvert.core.errors import Unsupported
from ringconvert.core.image import open_image

IMAGE_TARGETS = ["png", "webp", "heic", "gif", "avif", "bmp", "tiff"]


@needs("pillow_heif")
@pytest.mark.parametrize("target", IMAGE_TARGETS)
def test_jpg_to_image(jpg, target):
    [out] = convert(jpg, target).outputs
    assert out.name == f"Photo.{target}"
    img = open_image(out)
    assert img.size == (640, 480)
    # Spot-check a pixel inside the red circle survived.
    r, g, b = img.convert("RGB").getpixel((270, 230))
    assert r > 180 and g < 120 and b < 100


@needs("pillow_heif")
@pytest.mark.parametrize("target", ["jpg", "webp", "heic", "avif", "gif", "bmp", "tiff"])
def test_transparent_png(png_rgba, target):
    if target == "avif" and not (backends.pillow_supports_avif() or backends.which("avifenc")):
        pytest.skip("the ffmpeg AVIF fallback can't keep alpha; needs avifenc or Pillow AVIF")
    [out] = convert(png_rgba, target).outputs
    img = open_image(out).convert("RGBA")
    assert img.size == (300, 200)
    corner = img.getpixel((2, 2))
    if target in ("jpg", "bmp"):
        assert corner[:3] == (255, 255, 255)  # flattened onto white
    else:
        assert corner[3] < 50  # still transparent


@needs("pillow_heif")
@pytest.mark.parametrize("fmt", ["heic", "avif", "webp"])
def test_modern_formats_as_input(jpg, fmt):
    [middle] = convert(jpg, fmt).outputs
    [out] = convert(middle, "png").outputs
    assert Image.open(out).size == (640, 480)


def test_animation_kept(gif_animated):
    [out] = convert(gif_animated, "webp").outputs
    assert getattr(Image.open(out), "n_frames", 1) == 4


@needs("img2pdf")
def test_image_to_pdf(jpg, png_rgba):
    import pymupdf

    [out] = convert(jpg, "pdf").outputs
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1
        # img2pdf embeds the JPEG untouched (lossless).
        xref = doc[0].get_images()[0][0]
        assert doc.extract_image(xref)["ext"] == "jpeg"
    [out] = convert(png_rgba, "pdf").outputs
    with pymupdf.open(out) as doc:
        assert doc.page_count == 1


@needs("docx")
def test_image_to_docx(jpg):
    import docx

    [out] = convert(jpg, "docx").outputs
    assert len(docx.Document(out).inline_shapes) == 1


def test_same_format_refused(jpg):
    with pytest.raises(Unsupported):
        convert(jpg, "jpeg")
