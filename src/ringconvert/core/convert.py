# SPDX-License-Identifier: GPL-3.0-or-later
"""Single entry point used by the CLI and the GUI: convert(path, target)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import image, media, office, pdf
from .detect import IMAGE_FORMATS, FileInfo, detect, targets
from .errors import Unsupported
from .naming import Result

Progress = Callable[[float], None] | None


def convert(path: str | Path, target: str, progress: Progress = None,
            info: FileInfo | None = None) -> Result:
    info = info or detect(path)
    target = target.lower().lstrip(".")
    target = {"jpeg": "jpg", "tif": "tiff", "heif": "heic", "markdown": "md", "htm": "html"}.get(target, target)
    if target == info.format:
        raise Unsupported(f"{info.path.name} is already {target.upper()}")
    if target not in targets(info):
        name = info.format.upper() if info.format else "this file"
        raise Unsupported(f"Can't convert {name} to {target.upper()}")

    src = info.path
    kind = info.kind
    if kind == "image":
        if target in IMAGE_FORMATS:
            return image.convert(src, target)
        if target == "pdf":
            return image.to_pdf(src)
        if target == "docx":
            return image.image_to_docx(src)
    elif kind == "pdf":
        if target in IMAGE_FORMATS:
            return pdf.to_images(src, target, progress)
        if target == "txt":
            return pdf.to_txt(src)
        if target == "docx":
            return pdf.to_docx(src, progress)
    elif kind in ("video", "audio"):
        return media.convert(src, target, progress)
    elif kind in ("document", "spreadsheet", "presentation", "text"):
        return office.convert(src, kind, info.format, target)
    raise Unsupported(f"Can't convert {info.format} to {target.upper()}")


def convert_many(paths: list[str | Path], target: str, progress: Progress = None) -> tuple[Result, list[tuple[Path, Exception]]]:
    """Batch convert; returns the combined result and per-file failures."""
    total = Result()
    failures = []
    for i, path in enumerate(paths):
        def sub(fraction: float, i=i) -> None:
            if progress:
                progress((i + fraction) / len(paths))

        try:
            total.extend(convert(path, target, sub))
        except Exception as exc:  # keep going with the rest of the batch
            failures.append((Path(path), exc))
        sub(1.0)
    return total, failures
