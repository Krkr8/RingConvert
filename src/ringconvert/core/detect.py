# SPDX-License-Identifier: GPL-3.0-or-later
"""File type detection and the per-type menus shown in the rings."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

# MIME type -> (kind, canonical format)
_MIME_FORMATS = {
    "application/pdf": ("pdf", "pdf"),
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/webp": ("image", "webp"),
    "image/heic": ("image", "heic"),
    "image/heif": ("image", "heic"),
    "image/avif": ("image", "avif"),
    "image/gif": ("image", "gif"),
    "image/bmp": ("image", "bmp"),
    "image/x-bmp": ("image", "bmp"),
    "image/tiff": ("image", "tiff"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("document", "docx"),
    "application/vnd.oasis.opendocument.text": ("document", "odt"),
    "application/msword": ("document", "doc"),
    "application/rtf": ("document", "rtf"),
    "text/rtf": ("document", "rtf"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("spreadsheet", "xlsx"),
    "application/vnd.oasis.opendocument.spreadsheet": ("spreadsheet", "ods"),
    "application/vnd.ms-excel": ("spreadsheet", "xls"),
    "text/csv": ("spreadsheet", "csv"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("presentation", "pptx"),
    "application/vnd.oasis.opendocument.presentation": ("presentation", "odp"),
    "application/vnd.ms-powerpoint": ("presentation", "ppt"),
    "text/markdown": ("text", "md"),
    "text/x-markdown": ("text", "md"),
    "text/html": ("text", "html"),
    "text/plain": ("text", "txt"),
    "video/mp4": ("video", "mp4"),
    "video/x-matroska": ("video", "mkv"),
    "video/webm": ("video", "webm"),
    "video/quicktime": ("video", "mov"),
    "audio/mpeg": ("audio", "mp3"),
    "audio/mp4": ("audio", "m4a"),
    "audio/x-m4a": ("audio", "m4a"),
    "audio/wav": ("audio", "wav"),
    "audio/x-wav": ("audio", "wav"),
    "audio/vnd.wave": ("audio", "wav"),
    "audio/ogg": ("audio", "ogg"),
    "audio/x-vorbis+ogg": ("audio", "ogg"),
    "audio/x-opus+ogg": ("audio", "ogg"),
    "audio/flac": ("audio", "flac"),
    "audio/x-flac": ("audio", "flac"),
}

# Extension -> canonical format, used when MIME detection is inconclusive.
_EXT_FORMATS = {
    "jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "heif": "heic", "htm": "html",
    "markdown": "md", "text": "txt", "oga": "ogg", "opus": "ogg",
}

FORMAT_KIND = {fmt: kind for kind, fmt in _MIME_FORMATS.values()}

IMAGE_FORMATS = ["png", "jpg", "webp", "heic", "gif", "avif", "bmp", "tiff"]
VIDEO_FORMATS = ["mp4", "mkv", "webm", "mov"]
AUDIO_FORMATS = ["mp3", "m4a", "wav", "ogg", "flac"]

# Ring segments, clockwise from the top. The centre hub is a live readout
# (hovered label / file size), never an option, so every format is a real
# segment. The source's own format is removed.
MAX_SEGMENTS = 9
_MENUS = {
    "image": ["png", "jpg", "webp", "heic", "gif", "avif", "bmp", "tiff", "pdf", "docx"],
    "pdf": ["docx", "jpg", "png", "txt", "tiff"],
    "document": ["pdf", "docx", "odt", "txt", "md", "html", "rtf"],
    "spreadsheet": ["pdf", "xlsx", "ods", "csv", "html"],
    "presentation": ["pdf", "pptx", "odp", "jpg", "png"],
    "text": ["pdf", "docx", "odt", "html", "md", "txt"],
    "video": ["mp4", "webm", "mkv", "mov", "gif", "mp3", "m4a", "wav"],
    "audio": ["mp3", "m4a", "wav", "ogg", "flac"],
}

# Formats LibreOffice can read but not write back (no ".doc" output etc.).
_NO_OUTPUT = {"doc", "xls", "ppt"}

# Tools ring, clockwise from the top.
ALL_TOOLS = ["compress", "metadata", "edit", "annotate", "add_bg", "crop", "redact"]

_TOOLS = {
    "image": ALL_TOOLS,
    "pdf": ["compress", "metadata", "annotate", "redact"],
    "video": ["compress", "metadata"],
    "audio": ["compress", "metadata"],
    "document": ["metadata"],
    "spreadsheet": ["metadata"],
    "presentation": ["metadata"],
}


@dataclass(frozen=True)
class FileInfo:
    path: Path
    mime: str
    kind: str | None  # image, pdf, document, spreadsheet, presentation, text, video, audio
    format: str | None  # canonical extension, e.g. "jpg"


def _guess_mime(path: Path) -> str:
    """Content-sniffing MIME guess via GIO, falling back to the extension."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        data = None
        try:
            with open(path, "rb") as fh:
                data = fh.read(4096)
        except OSError:
            pass
        # Sniff the bytes first so a mis-named file is still handled right;
        # fall back to the name for formats content can't pin down (zip-based
        # office files, Markdown vs. plain text, ...).
        ctype, uncertain = Gio.content_type_guess(None, data) if data else (None, True)
        generic = ("text/plain", "application/zip", "application/octet-stream", "application/xml")
        mime = Gio.content_type_get_mime_type(ctype) if ctype else None
        if uncertain or not mime or mime in generic:
            ctype, _ = Gio.content_type_guess(str(path), data)
            mime = Gio.content_type_get_mime_type(ctype) if ctype else mime
        if mime:
            return mime
    except (ImportError, ValueError):
        pass
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def detect(path: str | Path, mime: str | None = None) -> FileInfo:
    """`mime`: a type already sniffed by the caller (e.g. Nautilus), to skip reading the file."""
    path = Path(path)
    mime = mime or _guess_mime(path)
    kind, fmt = _MIME_FORMATS.get(mime, (None, None))
    ext = path.suffix.lower().lstrip(".")
    ext = _EXT_FORMATS.get(ext, ext)
    # Plain-text sniffing can't tell Markdown from text, and zipped office
    # files sometimes sniff as application/zip: trust a known extension then.
    if ext in FORMAT_KIND and (kind is None or kind == "text" or mime == "application/zip"):
        kind, fmt = FORMAT_KIND[ext], ext
    return FileInfo(path=path, mime=mime, kind=kind, format=fmt)


def targets(info: FileInfo) -> list[str]:
    """Convert-ring formats for this file, clockwise from the top."""
    exclude = {info.format} | _NO_OUTPUT
    return [f for f in _MENUS.get(info.kind, []) if f not in exclude][:MAX_SEGMENTS]


def tools(info: FileInfo) -> list[str]:
    """Tools-ring entries applicable to this file, clockwise from the top."""
    ring = list(_TOOLS.get(info.kind, []))
    # Uncompressed WAV can't be meaningfully "compressed" without changing format.
    if info.kind == "audio" and info.format == "wav":
        ring.remove("compress")
    return ring


def _common(lists: list[list[str]]) -> list[str]:
    if not lists:
        return []
    rest = [set(other) for other in lists[1:]]
    return [item for item in lists[0] if all(item in other for other in rest)]


def common_targets(infos: list[FileInfo]) -> list[str]:
    """Formats every file in a batch can be converted to (first file's order)."""
    return _common([targets(i) for i in infos])


def common_tools(infos: list[FileInfo]) -> list[str]:
    return _common([tools(i) for i in infos])


def label(item: str) -> str:
    """Segment label: "add_bg" -> "ADD BG", "jpg" -> "JPG"."""
    return item.replace("_", " ").upper()
