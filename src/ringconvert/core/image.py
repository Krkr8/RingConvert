# SPDX-License-Identifier: GPL-3.0-or-later
"""Image <-> image conversion with Pillow (+ pillow-heif, AVIF fallbacks)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import backends
from .errors import BackendMissing, RingConvertError
from .naming import Result, discard, output_path

PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "gif": "GIF",
    "bmp": "BMP", "tiff": "TIFF", "heic": "HEIF", "avif": "AVIF",
}
# Formats that can hold an animation when the source has several frames.
ANIMATED = {"gif", "webp", "png"}
_NO_ALPHA = {"jpg", "bmp"}

_heif_registered = False


def _register_heif() -> bool:
    global _heif_registered
    if not _heif_registered and backends.has_module("pillow_heif"):
        import pillow_heif

        pillow_heif.register_heif_opener()
        _heif_registered = True
    return _heif_registered


def _avif_via_cli(src: Path, dst: Path, decode: bool) -> None:
    """AVIF encode/decode when Pillow lacks AVIF: avifenc/avifdec, then ffmpeg."""
    tool = "avifdec" if decode else "avifenc"
    if backends.which(tool):
        cmd = [tool, str(src), str(dst)] if decode else [tool, "-q", "80", str(src), str(dst)]
    elif backends.which("ffmpeg"):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
        if not decode:
            encoder = backends.first_encoder("libaom-av1", "libsvtav1", "librav1e")
            if not encoder:
                raise BackendMissing("AVIF encoder", backends.HINTS["avifenc"])
            cmd += ["-frames:v", "1", "-c:v", encoder, "-still-picture", "1", "-crf", "30"]
            if encoder == "libaom-av1":
                cmd += ["-cpu-used", "6"]
        cmd.append(str(dst))
    else:
        raise BackendMissing("avifenc", backends.HINTS["avifenc"])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RingConvertError(f"AVIF conversion failed: {proc.stderr.strip()[-300:]}")


def open_image(path: Path):
    """Open any supported image with Pillow (frames and EXIF left intact)."""
    Image = backends.require_module("PIL.Image")
    suffix = path.suffix.lower()
    if suffix == ".avif" and not backends.pillow_supports_avif():
        with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
            png = Path(tmp) / "decoded.png"
            _avif_via_cli(path, png, decode=True)
            img = Image.open(png)
            img.load()
            return img
    heif_ok = _register_heif()
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        if suffix in (".heic", ".heif") and not heif_ok:
            raise BackendMissing("pillow_heif", backends.HINTS["pillow_heif"]) from exc
        raise RingConvertError(f"Can't open {path.name}: {exc}") from exc
    return img


def _upright(img):
    """Apply EXIF orientation to a single-frame image."""
    from PIL import ImageOps

    if getattr(img, "n_frames", 1) > 1:
        return img
    return ImageOps.exif_transpose(img)


def _flatten(img, background=(255, 255, 255)):
    """RGB copy of `img`, compositing any transparency onto `background`."""
    from PIL import Image

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, background)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")


def _prepare(img, fmt: str):
    """Convert the pixel mode to something `fmt` can store."""
    if fmt in _NO_ALPHA:
        return _flatten(img)
    if fmt == "gif":
        # Pillow's GIF writer builds the palette (and transparency) itself.
        return img if img.mode in ("P", "L", "RGB", "RGBA") else img.convert("RGBA")
    if fmt in ("heic", "avif", "webp"):
        return img.convert("RGBA") if "A" in img.getbands() or "transparency" in img.info else img.convert("RGB")
    if img.mode in ("I;16", "I", "F") and fmt != "tiff":
        return img.convert("RGB")
    if img.mode == "CMYK" and fmt != "tiff":
        return img.convert("RGB")
    return img


def save_image(img, dst: Path, fmt: str, *, quality: int = 92, keep_metadata: bool = True,
               optimize: bool = False, all_frames=None) -> None:
    """Save a Pillow image as `fmt` (our canonical extension) at `dst`."""
    fmt = fmt.lower()
    kwargs: dict = {}
    if keep_metadata:
        icc = img.info.get("icc_profile")
        if icc and fmt not in ("gif", "bmp"):
            kwargs["icc_profile"] = icc
        if fmt in ("jpg", "png", "webp", "tiff", "heic", "avif"):
            exif = img.getexif()
            if exif:
                kwargs["exif"] = exif.tobytes()
    if fmt in ("jpg", "webp", "heic", "avif"):
        kwargs["quality"] = quality
    if fmt == "jpg":
        kwargs["optimize"] = True
        if quality >= 90:
            kwargs["subsampling"] = 0
    if fmt == "png" and optimize:
        kwargs["optimize"] = True
    if fmt == "tiff":
        kwargs["compression"] = "tiff_lzw"

    frames = None
    if all_frames is not None and fmt in ANIMATED and getattr(all_frames, "n_frames", 1) > 1:
        frames = _frames(all_frames, fmt)
    if fmt == "heic" and not _register_heif():
        raise BackendMissing("pillow_heif", backends.HINTS["pillow_heif"])

    if fmt == "avif" and not backends.pillow_supports_avif():
        with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
            png = Path(tmp) / "src.png"
            _prepare(img, "png").save(png, "PNG")
            _avif_via_cli(png, dst, decode=False)
        return

    if frames:
        first, rest, durations = frames
        first.save(dst, PIL_FORMAT[fmt], save_all=True, append_images=rest,
                   duration=durations, loop=all_frames.info.get("loop", 0), **kwargs)
    else:
        _prepare(img, fmt).save(dst, PIL_FORMAT[fmt], **kwargs)


def _frames(img, fmt: str):
    from PIL import ImageSequence

    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        durations.append(frame.info.get("duration", 100))
        frames.append(_prepare(frame.convert("RGBA"), fmt))
    return frames[0], frames[1:], durations


def convert(src: Path, fmt: str) -> Result:
    result = Result()
    img = open_image(src)
    animated = getattr(img, "n_frames", 1) > 1
    dst = output_path(src, fmt, result=result)
    try:
        save_image(_upright(img), dst, fmt, all_frames=img if animated else None)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def to_pdf(src: Path) -> Result:
    """Image -> PDF, losslessly with img2pdf where the format allows."""
    result = Result()
    dst = output_path(src, "pdf", result=result)
    try:
        with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
            img2pdf = backends.require_module("img2pdf")
            img = open_image(src)
            source = src
            # img2pdf embeds JPEG/PNG/TIFF bytes as-is, but rejects alpha and
            # formats it can't parse; re-encode those as lossless PNG first.
            needs_png = (
                src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff")
                or "A" in img.getbands()
                or "transparency" in img.info
                or img.mode not in ("RGB", "L", "1", "P", "CMYK")
            )
            if needs_png:
                source = Path(tmp) / "page.png"
                _flatten(_upright(img)).save(source, "PNG")
            dst.write_bytes(img2pdf.convert(str(source), rotation=img2pdf.Rotation.ifvalid))
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def to_docx(srcs: list[Path], dst: Path) -> None:
    """Write a DOCX with each image on its own page, scaled to fit."""
    docx = backends.require_module("docx")
    from docx.enum.section import WD_ORIENT
    from docx.shared import Emu

    document = docx.Document()
    section = document.sections[0]
    with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
        for i, src in enumerate(srcs):
            img = _upright(open_image(src))
            w, h = img.size
            if i == 0 and w > h:
                section.orientation = WD_ORIENT.LANDSCAPE
                section.page_width, section.page_height = section.page_height, section.page_width
            avail_w = section.page_width - section.left_margin - section.right_margin
            avail_h = section.page_height - section.top_margin - section.bottom_margin
            scale = min(avail_w / w, avail_h / h)
            png = Path(tmp) / f"{i}.png"
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA")
            img.save(png, "PNG")
            if i:
                document.add_page_break()
            document.add_picture(str(png), width=Emu(int(w * scale)), height=Emu(int(h * scale)))
    document.save(dst)


def image_to_docx(src: Path) -> Result:
    result = Result()
    dst = output_path(src, "docx", result=result)
    try:
        to_docx([src], dst)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result
