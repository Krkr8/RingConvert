# SPDX-License-Identifier: GPL-3.0-or-later
"""PDF conversions and tools with PyMuPDF (+ pdf2docx)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from . import backends
from .errors import RingConvertError
from .image import save_image
from .naming import Result, discard, output_dir, output_path, unique_path

Progress = Callable[[float], None] | None

DPI = 200


def _open(src: Path):
    pymupdf = backends.require_module("pymupdf")
    try:
        doc = pymupdf.open(src)
    except Exception as exc:
        raise RingConvertError(f"Can't open {src.name}: {exc}") from exc
    if doc.needs_pass:
        raise RingConvertError(f"{src.name} is password protected")
    return doc


def open_pdf(src: Path):
    """An open PyMuPDF document (close it, or use it as a context manager)."""
    return _open(src)


def render_page(doc, index: int, dpi: int = DPI, alpha: bool = False):
    """Render one page to a Pillow image."""
    from PIL import Image

    pix = doc[index].get_pixmap(dpi=dpi, alpha=alpha)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def to_images(src: Path, fmt: str, progress: Progress = None, dpi: int = DPI,
              name_like: Path | None = None) -> Result:
    """One image per page: `Name.jpg`, or `Name 1.jpg`, `Name 2.jpg`, ...

    Outputs are named and placed after `name_like` (default: `src`), so an
    intermediate PDF in a temp folder can produce images next to the original.
    """
    result = Result()
    name_like = name_like or src
    with _open(src) as doc:
        count = doc.page_count
        if count == 0:
            raise RingConvertError(f"{src.name} has no pages")
        folder = output_dir(name_like, result)
        for i in range(count):
            stem = name_like.stem if count == 1 else f"{name_like.stem} {i + 1}"
            dst = unique_path(folder, stem, fmt)
            try:
                save_image(render_page(doc, i, dpi), dst, fmt, quality=90)
            except BaseException:
                discard(dst)
                raise
            result.outputs.append(dst)
            if progress:
                progress((i + 1) / count)
    return result


def to_txt(src: Path) -> Result:
    result = Result()
    with _open(src) as doc:
        text = "\n\f".join(page.get_text("text", sort=True) for page in doc)
    if not text.strip():
        result.notes.append(f"{src.name} has no text layer (it may be a scan)")
    dst = output_path(src, "txt", result=result)
    dst.write_text(text, encoding="utf-8")
    result.outputs.append(dst)
    return result


def to_docx(src: Path, progress: Progress = None) -> Result:
    pdf2docx = backends.require_module("pdf2docx")
    result = Result()
    dst = output_path(src, "docx", result=result)
    try:
        converter = pdf2docx.Converter(str(src))
        try:
            converter.convert(str(dst))
        finally:
            converter.close()
    except BaseException as exc:
        discard(dst)
        if isinstance(exc, Exception) and not isinstance(exc, RingConvertError):
            raise RingConvertError(f"PDF to DOCX failed: {exc}") from exc
        raise
    if progress:
        progress(1.0)
    result.outputs.append(dst)
    return result


def _save(doc, dst: Path) -> None:
    doc.save(dst, garbage=4, deflate=True, clean=True)


def strip_metadata(src: Path, dst: Path) -> None:
    """Remove the info dictionary and XMP metadata."""
    with _open(src) as doc:
        doc.set_metadata({})
        doc.del_xml_metadata()
        _save(doc, dst)


def read_metadata(src: Path) -> list[tuple[str, str, str]]:
    with _open(src) as doc:
        rows = [("PDF", key, str(value)) for key, value in (doc.metadata or {}).items() if value]
        rows.append(("PDF", "pages", str(doc.page_count)))
        if doc.get_xml_metadata():
            rows.append(("XMP", "xmp", "present"))
    return rows


def redact(src: Path, rects: list[tuple[int, float, float, float, float]]) -> Result:
    """True redaction: remove text/images/vector art under each box.

    `rects` are (page index, x, y, w, h) in PDF points, origin top-left.
    """
    pymupdf = backends.require_module("pymupdf")
    result = Result()
    dst = output_path(src, "pdf", " Redacted", result)
    try:
        with _open(src) as doc:
            pages = set()
            for page_no, x, y, w, h in rects:
                if not 0 <= page_no < doc.page_count:
                    raise RingConvertError(f"Page {page_no + 1} doesn't exist")
                page = doc[page_no]
                page.add_redact_annot(pymupdf.Rect(x, y, x + w, y + h), fill=(0, 0, 0))
                pages.add(page_no)
            for page_no in pages:
                doc[page_no].apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                )
            # Redacted files shouldn't leak authorship or earlier revisions either.
            doc.set_metadata({})
            doc.del_xml_metadata()
            _save(doc, dst)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def _rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def annotate(src: Path, shapes: list[dict]) -> Result:
    """Add real PDF annotations (still editable in other viewers).

    Each shape is a dict like the image annotator's, plus "page" (index);
    coordinates are PDF points, origin top-left. See imageops.annotate.
    """
    pymupdf = backends.require_module("pymupdf")
    result = Result()
    dst = output_path(src, "pdf", " Annotated", result)
    try:
        with _open(src) as doc:
            for shape in shapes:
                page = doc[int(shape.get("page", 0))]
                kind = shape["type"]
                color = _rgb(shape.get("color", "#e8462a"))
                width = float(shape.get("width", 3))
                pts = [pymupdf.Point(*p) for p in shape.get("points", [])]
                if kind == "rect":
                    annot = page.add_rect_annot(pymupdf.Rect(pts[0], pts[1]).normalize())
                    annot.set_colors(stroke=color)
                elif kind == "highlight":
                    annot = page.add_rect_annot(pymupdf.Rect(pts[0], pts[1]).normalize())
                    annot.set_colors(stroke=color, fill=color)
                    annot.set_opacity(0.35)
                    width = 0
                elif kind == "arrow":
                    annot = page.add_line_annot(pts[0], pts[1])
                    annot.set_colors(stroke=color, fill=color)
                    annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
                elif kind == "pen":
                    annot = page.add_ink_annot([[(p.x, p.y) for p in pts]])
                    annot.set_colors(stroke=color)
                elif kind == "text":
                    size = float(shape.get("size", 16))
                    text = shape.get("text", "")
                    lines = text.splitlines() or [""]
                    w = max(len(line) for line in lines) * size * 0.6 + size
                    h = len(lines) * size * 1.3 + size * 0.5
                    rect = pymupdf.Rect(pts[0].x, pts[0].y, pts[0].x + w, pts[0].y + h)
                    annot = page.add_freetext_annot(rect, text, fontsize=size, text_color=color)
                    annot.update()
                    continue
                else:
                    raise RingConvertError(f"Unknown annotation type {kind!r}")
                annot.set_border(width=width)
                annot.update()
            _save(doc, dst)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def page_count(src: Path) -> int:
    with _open(src) as doc:
        return doc.page_count
