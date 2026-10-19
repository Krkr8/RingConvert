# SPDX-License-Identifier: GPL-3.0-or-later
"""COMPRESS tool: images re-encoded, PDFs through Ghostscript, media through ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from . import backends, media
from .detect import FileInfo
from .errors import RingConvertError, Unsupported
from .image import open_image, save_image
from .naming import Result, discard, output_path

Progress = Callable[[float], None] | None

IMAGE_QUALITY = 75


def _image(src: Path, fmt: str, dst: Path) -> None:
    from PIL import ImageOps

    img = open_image(src)
    if getattr(img, "n_frames", 1) > 1:
        save_image(img, dst, fmt, quality=IMAGE_QUALITY, keep_metadata=False,
                   optimize=True, all_frames=img)
        return
    img = ImageOps.exif_transpose(img)
    if fmt == "png":
        # Lossless PNG barely shrinks on re-encode; reduce to a 256-colour
        # palette like pngquant does, which keeps alpha.
        rgba = img.convert("RGBA")
        img = rgba.quantize(256, method=2, dither=1)
    save_image(img, dst, fmt, quality=IMAGE_QUALITY, keep_metadata=False, optimize=True)


def _pdf(src: Path, dst: Path) -> None:
    gs = backends.require_program("gs")
    cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dPDFSETTINGS=/ebook",
           "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER", f"-sOutputFile={dst}", str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RingConvertError(f"Ghostscript failed: {(proc.stderr or proc.stdout).strip()[-400:]}")


def compress(info: FileInfo, progress: Progress = None) -> Result:
    src = info.path
    ext = src.suffix.lstrip(".") or info.format or "bin"
    result = Result()
    if info.kind not in ("image", "pdf", "video", "audio"):
        raise Unsupported(f"Can't compress {info.format or 'this'} files")
    dst = output_path(src, ext, " Compressed", result)
    try:
        if info.kind == "image":
            _image(src, info.format, dst)
        elif info.kind == "pdf":
            _pdf(src, dst)
        else:
            media.compress(src, dst, progress)
    except BaseException:
        discard(dst)
        raise
    before, after = src.stat().st_size, dst.stat().st_size
    if after >= before:
        result.notes.append(f"{src.name} was already well compressed; the copy isn't smaller")
    else:
        result.notes.append(f"{_size(before)} → {_size(after)} ({100 - after * 100 // before}% smaller)")
    result.outputs.append(dst)
    return result


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
