# SPDX-License-Identifier: GPL-3.0-or-later
"""METADATA tool: list tags and write a stripped " Clean" copy.

exiftool is preferred for reading and for images; PDFs and media use PyMuPDF
and ffmpeg for stripping since exiftool's PDF edits are reversible.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import backends, media, pdf
from .detect import FileInfo
from .errors import RingConvertError, Unsupported
from .naming import Result, discard, output_path

Tag = tuple[str, str, str]  # (group, name, value)

# exiftool groups that describe the file itself rather than embedded metadata.
_FILE_GROUPS = {"System", "File", "ExifTool", "Composite"}


def read(info: FileInfo) -> list[Tag]:
    if backends.which("exiftool"):
        return _exiftool_read(info.path)
    if info.kind == "pdf":
        return pdf.read_metadata(info.path)
    if info.kind in ("video", "audio"):
        return media.read_metadata(info.path)
    if info.kind == "image":
        return _pillow_read(info.path)
    raise Unsupported("Reading this file's metadata needs exiftool ("
                      + backends.HINTS["exiftool"] + ")")


def _exiftool_read(path: Path) -> list[Tag]:
    proc = subprocess.run(
        ["exiftool", "-json", "-G1", "-a", "-s", "-n", "-x", "SourceFile", "--", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RingConvertError(f"exiftool failed: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "[{}]")[0]
    rows = []
    for key, value in data.items():
        if key == "SourceFile":  # JSON output always includes the path, even with -x
            continue
        group, _, name = key.partition(":")
        if not name:
            group, name = "", group
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        rows.append((group, name, str(value)))
    return rows


def _pillow_read(path: Path) -> list[Tag]:
    from PIL import ExifTags

    from .image import open_image

    img = open_image(path)
    rows = [("Image", "size", f"{img.width}x{img.height}"), ("Image", "mode", img.mode)]
    exif = img.getexif()
    for tag_id, value in exif.items():
        rows.append(("EXIF", ExifTags.TAGS.get(tag_id, str(tag_id)), str(value)))
    for ifd_id in (ExifTags.IFD.Exif, ExifTags.IFD.GPSInfo):
        for tag_id, value in exif.get_ifd(ifd_id).items():
            names = ExifTags.GPSTAGS if ifd_id == ExifTags.IFD.GPSInfo else ExifTags.TAGS
            rows.append(("EXIF", names.get(tag_id, str(tag_id)), str(value)))
    if "icc_profile" in img.info:
        rows.append(("ICC", "profile", "present"))
    return rows


def embedded_only(tags: list[Tag]) -> list[Tag]:
    """Drop rows that describe the file (size, dates on disk) not its metadata."""
    return [t for t in tags if t[0] not in _FILE_GROUPS]


def can_strip(info: FileInfo) -> bool:
    """Whether `strip` handles this kind of file (reading works for more)."""
    return info.kind in ("pdf", "video", "audio", "image") or info.format == "docx"


def strip(info: FileInfo) -> Result:
    src = info.path
    ext = src.suffix.lstrip(".") or info.format or "bin"
    result = Result()
    dst = output_path(src, ext, " Clean", result)
    try:
        if info.kind == "pdf":
            pdf.strip_metadata(src, dst)
        elif info.kind in ("video", "audio"):
            media.strip_metadata(src, dst)
        elif info.kind == "image" and backends.which("exiftool"):
            _exiftool_strip(src, dst)
        elif info.kind == "image":
            _pillow_strip(info, dst)
            result.notes.append("exiftool isn't installed; the image was re-encoded to strip it")
        elif info.format == "docx":
            _docx_strip(src, dst)
        else:
            raise Unsupported(f"Can't strip metadata from {info.format or 'this'} files")
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def _exiftool_strip(src: Path, dst: Path) -> None:
    # exiftool refuses to write onto an existing file, and ours is reserved.
    dst.unlink()
    proc = subprocess.run(
        ["exiftool", "-q", "-all=", "-tagsfromfile", "@", "-Orientation", "-ICC_Profile",
         "-o", str(dst), "--", str(src)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dst.exists():
        dst.touch()  # keep the reservation so the caller's cleanup is uniform
        raise RingConvertError(f"exiftool failed: {proc.stderr.strip()[-300:]}")


def _pillow_strip(info: FileInfo, dst: Path) -> None:
    from PIL import ImageOps

    from .image import open_image, save_image

    img = open_image(info.path)
    if getattr(img, "n_frames", 1) > 1:
        save_image(img, dst, info.format, quality=95, keep_metadata=False, all_frames=img)
    else:
        save_image(ImageOps.exif_transpose(img), dst, info.format, quality=95, keep_metadata=False)


def _docx_strip(src: Path, dst: Path) -> None:
    docx = backends.require_module("docx")
    shutil.copyfile(src, dst)
    document = docx.Document(str(dst))
    props = document.core_properties
    for name in ("author", "last_modified_by", "comments", "keywords", "subject",
                 "title", "category", "content_status", "identifier", "version"):
        setattr(props, name, "")
    props.revision = 1
    document.save(str(dst))
