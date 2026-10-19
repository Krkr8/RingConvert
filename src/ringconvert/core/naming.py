# SPDX-License-Identifier: GPL-3.0-or-later
"""Output file naming: same folder, same base name, macOS-style " 2" on clashes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Result:
    """What a conversion or tool produced."""

    outputs: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def extend(self, other: "Result") -> None:
        self.outputs.extend(other.outputs)
        for note in other.notes:
            if note not in self.notes:
                self.notes.append(note)


def downloads_dir() -> Path:
    try:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if path:
            return Path(path)
    except Exception:
        pass
    return Path.home() / "Downloads"


def output_dir(src: Path, result: Result | None = None) -> Path:
    """The source's folder, or ~/Downloads if that folder isn't writable."""
    folder = src.resolve().parent
    if os.access(folder, os.W_OK | os.X_OK):
        return folder
    fallback = downloads_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    if result is not None:
        note = f"{folder} isn't writable, saved to {fallback}"
        if note not in result.notes:
            result.notes.append(note)
    return fallback


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    """`folder/stem.ext`, or `stem 2.ext`, `stem 3.ext`, ... if taken.

    The file is created empty to reserve the name, so two jobs running at the
    same time can't pick the same path.
    """
    ext = ext.lstrip(".")
    n = 1
    while True:
        name = f"{stem}.{ext}" if n == 1 else f"{stem} {n}.{ext}"
        path = folder / name
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return path


def output_path(src: Path, ext: str, suffix: str = "", result: Result | None = None) -> Path:
    """Reserve the output path for `src` converted to `ext`.

    `suffix` is appended to the base name, e.g. " Cropped".
    """
    return unique_path(output_dir(src, result), src.stem + suffix, ext)


def discard(path: Path) -> None:
    """Remove a reserved/partial output after a failure."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
