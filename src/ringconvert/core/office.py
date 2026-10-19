# SPDX-License-Identifier: GPL-3.0-or-later
"""Office documents (LibreOffice headless) and text markup (pandoc)."""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import backends
from .errors import RingConvertError
from .naming import Result, discard, output_path

LO_TIMEOUT = 300

# LibreOffice --convert-to filter per (source kind, target format).
_LO_FILTERS = {
    "pdf": "pdf",
    "docx": "docx:MS Word 2007 XML",
    "odt": "odt",
    "rtf": "rtf",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "ods": "ods",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "odp": "odp",
    "txt": "txt:Text (encoded):UTF8",
}
_LO_HTML = {"document": "html:XHTML Writer File:UTF8", "spreadsheet": "html:HTML (StarCalc)"}

# Formats pandoc reads/writes, by our extension.
_PANDOC_IN = {"docx": "docx", "odt": "odt", "md": "markdown", "html": "html", "rtf": "rtf"}
_PANDOC_OUT = {"docx": "docx", "odt": "odt", "md": "gfm", "html": "html", "txt": "plain"}


def _move_into(tmp_file: Path, dst: Path) -> None:
    shutil.move(str(tmp_file), str(dst))


def libreoffice(src: Path, target: str, out_dir: Path, kind: str | None = None) -> Path:
    """Run LibreOffice headless and return the produced file in `out_dir`."""
    soffice = backends.require_program("libreoffice")
    if target == "html" and kind in _LO_HTML:
        filt = _LO_HTML[kind]
    else:
        filt = _LO_FILTERS.get(target, target)
    # A private profile lets this work even while the user has LibreOffice
    # open (otherwise the call is handed to the running instance and returns
    # without converting anything).
    profile = Path(tempfile.mkdtemp(prefix="ringconvert-lo-"))
    try:
        cmd = [
            soffice, f"-env:UserInstallation={profile.as_uri()}",
            "--headless", "--norestore", "--nologo", "--nolockcheck",
            "--convert-to", filt, "--outdir", str(out_dir), str(src),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=LO_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise RingConvertError("LibreOffice timed out") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    produced = out_dir / f"{src.stem}.{target}"
    if not produced.exists():
        detail = (proc.stderr or proc.stdout).strip()[-400:]
        raise RingConvertError(f"LibreOffice couldn't convert {src.name} to {target.upper()}: {detail}")
    return produced


def pandoc(src: Path, src_fmt: str, target: str, dst: Path) -> None:
    exe = backends.require_program("pandoc")
    cmd = [exe, "-f", _PANDOC_IN[src_fmt], "-t", _PANDOC_OUT[target], "-o", str(dst), str(src)]
    if target == "html":
        cmd[1:1] = ["--standalone", "--embed-resources", "--metadata", f"title={src.stem}"]
    if target == "md":
        cmd[1:1] = ["--wrap=none"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=src.parent)
    if proc.returncode != 0:
        raise RingConvertError(f"pandoc failed: {proc.stderr.strip()[-400:]}")


def _txt_to_html(src: Path, dst: Path) -> None:
    text = src.read_text(encoding="utf-8", errors="replace")
    title = html.escape(src.stem)
    dst.write_text(
        f"<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>{title}</title></head>\n"
        f"<body><pre style=\"white-space: pre-wrap; font-family: inherit\">{html.escape(text)}</pre></body></html>\n",
        encoding="utf-8",
    )


def convert(src: Path, kind: str, src_fmt: str, target: str) -> Result:
    """Convert an office/markup document; picks pandoc or LibreOffice per pair."""
    if kind == "presentation" and target in ("jpg", "png"):
        # One image per slide via an intermediate PDF.
        from . import pdf as pdfmod

        with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
            staged = libreoffice(src, "pdf", Path(tmp), kind)
            return pdfmod.to_images(staged, target, name_like=src)

    result = Result()
    dst = output_path(src, target, result=result)
    try:
        with tempfile.TemporaryDirectory(prefix="ringconvert-") as tmp:
            tmp = Path(tmp)
            if kind == "text":
                _convert_text(src, src_fmt, target, dst, tmp)
            elif kind == "document" and target == "md":
                if src_fmt not in _PANDOC_IN:
                    src, src_fmt = libreoffice(src, "docx", tmp, kind), "docx"
                pandoc(src, src_fmt, target, dst)
            elif kind == "document" and target in ("html", "txt") and src_fmt in _PANDOC_IN \
                    and backends.which("pandoc"):
                pandoc(src, src_fmt, target, dst)
            else:
                _move_into(libreoffice(src, target, tmp, kind), dst)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def _convert_text(src: Path, src_fmt: str, target: str, dst: Path, tmp: Path) -> None:
    """Markdown/HTML/plain text -> docx/odt/html/md/txt/pdf."""
    if target == "pdf":
        # pandoc's PDF engines need LaTeX & friends; DOCX -> LibreOffice -> PDF
        # gives a nicely typeset page with what's already installed.
        if src_fmt == "txt":
            pdf = libreoffice(src, "pdf", tmp, "document")
        else:
            middle = tmp / f"{src.stem}.docx"
            pandoc(src, src_fmt, "docx", middle)
            pdf = libreoffice(middle, "pdf", tmp, "document")
        _move_into(pdf, dst)
    elif src_fmt == "txt":
        if target == "html":
            _txt_to_html(src, dst)
        elif target == "md":
            shutil.copyfile(src, dst)
        else:
            _move_into(libreoffice(src, target, tmp, "document"), dst)
    else:
        pandoc(src, src_fmt, target, dst)
