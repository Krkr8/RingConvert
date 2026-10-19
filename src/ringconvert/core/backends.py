# SPDX-License-Identifier: GPL-3.0-or-later
"""Availability checks for external programs and optional Python modules.

Every backend is optional: callers ask `require_*` right before they need
something, so a missing tool only disables the conversions that depend on it.
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import logging
import shutil
import subprocess
import sys

from .errors import BackendMissing

# Fedora package (or pip package) that provides each backend, used in hints.
HINTS = {
    "ffmpeg": "dnf install ffmpeg (RPM Fusion) or ffmpeg-free",
    "ffprobe": "comes with ffmpeg",
    "libreoffice": "dnf install libreoffice-writer libreoffice-calc libreoffice-impress",
    "pandoc": "dnf install pandoc-cli",
    "exiftool": "dnf install perl-Image-ExifTool",
    "gs": "dnf install ghostscript",
    "avifenc": "dnf install libavif-tools",
    "avifdec": "dnf install libavif-tools",
    "pymupdf": "dnf install python3-PyMuPDF",
    "pdf2docx": "pip install --user pdf2docx",
    "docx": "pip install --user python-docx",
    "pillow_heif": "pip install --user pillow-heif",
    "img2pdf": "dnf install python3-img2pdf",
    "PIL": "dnf install python3-pillow",
    # Extras Fedora's own packages lack; the fallbacks cover them.
    "pillow avif": "optional: avifenc is used instead",
    "ffmpeg libx264": "optional: RPM Fusion's ffmpeg; OpenH264 is used instead",
}

_PROGRAM_NAMES = {
    "libreoffice": ("libreoffice", "soffice"),
}


@functools.cache
def which(name: str) -> str | None:
    for candidate in _PROGRAM_NAMES.get(name, (name,)):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def require_program(name: str) -> str:
    path = which(name)
    if not path:
        raise BackendMissing(name, HINTS.get(name, ""))
    return path


@functools.cache
def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def require_module(name: str):
    try:
        if name == "pdf2docx":
            return _import_pdf2docx()
        return importlib.import_module(name)
    except Exception as exc:
        raise BackendMissing(name, HINTS.get(name, "")) from exc


def _import_pdf2docx():
    """Import pdf2docx without its side effects on the terminal.

    It imports PyMuPDF under the deprecated name "fitz" (which prints a
    warning) and calls logging.basicConfig(INFO) at import time.
    """
    if "pdf2docx" in sys.modules:
        return sys.modules["pdf2docx"]
    sys.modules.setdefault("fitz", importlib.import_module("pymupdf"))
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        module = importlib.import_module("pdf2docx")
    finally:
        for handler in root.handlers[:]:
            if handler not in handlers:
                root.removeHandler(handler)
        root.setLevel(level)
    logging.getLogger("pikepdf").setLevel(logging.WARNING)
    return module


@functools.cache
def ffmpeg_encoders() -> frozenset[str]:
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return frozenset()
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # Encoder lines look like " V....D libx264  description".
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def first_encoder(*candidates: str) -> str | None:
    available = ffmpeg_encoders()
    for name in candidates:
        if name in available:
            return name
    return None


@functools.cache
def pillow_supports_avif() -> bool:
    try:
        from PIL import features

        return bool(features.check("avif"))
    except Exception:
        return False


def status() -> dict[str, bool]:
    """Backend name -> installed, for `ringconvert-cli --check`."""
    result = {}
    for prog in ("ffmpeg", "ffprobe", "libreoffice", "pandoc", "exiftool", "gs", "avifenc"):
        result[prog] = which(prog) is not None
    for mod in ("PIL", "pymupdf", "pdf2docx", "docx", "pillow_heif", "img2pdf"):
        result[mod] = has_module(mod)
    result["pillow avif"] = pillow_supports_avif()
    result["ffmpeg libx264"] = "libx264" in ffmpeg_encoders()
    return result
