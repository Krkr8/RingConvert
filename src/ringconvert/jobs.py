# SPDX-License-Identifier: GPL-3.0-or-later
"""Run conversions off the UI thread and report the outcome as a notification."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import APP_ID  # noqa: E402
from .core.convert import convert_many  # noqa: E402
from .core.detect import label  # noqa: E402
from .core.naming import Result  # noqa: E402

Failures = list[tuple[Path, Exception]]

# Notification titles for tools: (done, failed).
TOOL_TITLES = {
    "crop": ("Cropped", "Couldn't crop"),
    "add_bg": ("Added a background", "Couldn't add a background"),
    "compress": ("Compressed", "Couldn't compress"),
    "metadata": ("Removed metadata", "Couldn't remove metadata"),
    "edit": ("Edited", "Couldn't edit"),
    "annotate": ("Annotated", "Couldn't annotate"),
    "redact": ("Redacted", "Couldn't redact"),
}

Work = Callable[[Path, Callable[[float], None]], Result]


class Job:
    """Converts `paths` to `target` in a thread; callbacks run on the main loop.

    With `work`, runs `work(path, progress) -> Result` on each path instead
    (a tool, its settings from a dialog); `target` is then the tool's name and
    `progress(fraction)` reports progress within that one file.
    """

    def __init__(self, paths: list[Path], target: str,
                 on_progress: Callable[[float], None],
                 on_done: Callable[["Job", Result, Failures], None],
                 work: Work | None = None):
        self.paths = paths
        self.target = target
        self.on_progress = on_progress
        self.on_done = on_done
        self.work = work
        self._last_percent = -1

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"convert-{self.target}", daemon=True).start()

    def _progress(self, fraction: float) -> None:
        percent = int(fraction * 100)
        if percent != self._last_percent:  # don't flood the main loop
            self._last_percent = percent
            GLib.idle_add(self.on_progress, fraction)

    def _run(self) -> None:
        try:
            if self.work:
                result, failures = self._run_work()
            else:
                result, failures = convert_many(self.paths, self.target, self._progress)
        except Exception as exc:  # convert_many catches per file; this is a bug guard
            result, failures = Result(), [(self.paths[0], exc)]
        GLib.idle_add(self.on_done, self, result, failures)

    def _run_work(self) -> tuple[Result, Failures]:
        result, failures = Result(), []
        for i, path in enumerate(self.paths):
            def sub(fraction: float, i=i) -> None:
                self._progress((i + fraction) / len(self.paths))

            try:
                result.extend(self.work(Path(path), sub))
            except Exception as exc:  # keep going with the rest of the batch
                failures.append((Path(path), exc))
            self._progress((i + 1) / len(self.paths))
        return result, failures


def _names(paths: list[Path], limit: int = 3) -> str:
    names = [p.name for p in paths]
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


def build_notification(job: Job, result: Result, failures: Failures) -> Gio.Notification:
    fmt = label(job.target)
    done_title, failed_title = TOOL_TITLES.get(
        job.target, (f"Converted to {fmt}", f"Couldn't convert to {fmt}"))
    if failures and not result.outputs:
        title = failed_title
        lines = [f"{path.name}: {exc}" for path, exc in failures[:3]]
        priority = Gio.NotificationPriority.HIGH
    else:
        if failures:
            done = len(job.paths) - len(failures)
            title = (f"{done_title}: {done} of {len(job.paths)} files" if job.target in TOOL_TITLES
                     else f"Converted {done} of {len(job.paths)} files to {fmt}")
        else:
            title = done_title
        lines = [_names(result.outputs)]
        lines += [f"{path.name}: {exc}" for path, exc in failures[:2]]
        priority = Gio.NotificationPriority.NORMAL
    lines += result.notes[:2]
    notification = Gio.Notification.new(title)
    notification.set_body("\n".join(lines))
    notification.set_icon(Gio.ThemedIcon.new(APP_ID))
    notification.set_priority(priority)
    if result.outputs:
        # Handled by the app's "show-in-files" action; GNOME starts the app
        # over D-Bus for it if it has quit since.
        uris = GLib.Variant("as", [p.resolve().as_uri() for p in result.outputs])
        notification.add_button_with_target("Show in Files", "app.show-in-files", uris)
        notification.set_default_action_and_target("app.show-in-files", uris)
    return notification
