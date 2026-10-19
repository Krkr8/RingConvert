# SPDX-License-Identifier: GPL-3.0-or-later
"""The GNOME Shell extension, end to end, in a headless GNOME Shell that is
isolated from the user's session (tests/shell_harness.py): a real Wayland drag
from a GTK window, Shift/Escape and the pointer injected through that shell's
own RemoteDesktop API, and the real app on the nested session bus.

Starts a whole compositor (~30 s for the module), so it's opt-in:
    RINGCONVERT_SHELL_TESTS=1 python3 -m pytest -q tests/test_shell_extension.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from shell_harness import HEIGHT, UUID, WIDTH, HeadlessShell, available, mounts_under

reason = available() or (None if os.environ.get("RINGCONVERT_SHELL_TESTS") == "1"
                         else "set RINGCONVERT_SHELL_TESTS=1 to run a headless GNOME Shell")
pytestmark = pytest.mark.skipif(bool(reason), reason=reason or "")

APP_ID = "io.github.ringconvert.RingConvert"
SECOND = (800, 600)  # second monitor, laid out right of the first
SLACK = 30  # ring_widget.Geometry.margin: may hang off the work area
RING = 460  # ring window size


@pytest.fixture(scope="module")
def shell(tmp_path_factory):
    from PIL import Image

    work = tmp_path_factory.mktemp("shell")
    files = work / "files"
    files.mkdir()
    image = files / "Photo.png"
    Image.new("RGB", (64, 48), "#e8462a").save(image)
    real_runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    real_mounts = mounts_under(real_runtime)
    with HeadlessShell(work, monitors=(f"{WIDTH}x{HEIGHT}", "x".join(map(str, SECOND)))) as sh:
        sh.real_runtime, sh.real_mounts = real_runtime, real_mounts
        sh.wait_log("[ringconvert] enabled", timeout=30)
        sh.spawn([sys.executable, "-m", "ringconvert.app", "--service"], "app.log")
        sh.wait_for(lambda: sh.has_owner(APP_ID), "the app", 20)
        sh.spawn([sys.executable, str(Path(__file__).with_name("drag_source.py")), str(image)], "src.log")
        sh.wait_log("mapped", log="src.log", timeout=20)
        time.sleep(1)
        sh.image = image
        yield sh
        sh.input.button(False)


class Marks:
    """Log offsets at the start of a test, so assertions only see its own lines."""

    def __init__(self, shell):
        self.shell = shell
        self.shell_at = shell.log_size()
        self.app_at = shell.log_size("app.log")

    def ext(self, text, timeout=5):
        self.shell_at = self.shell.wait_log(text, self.shell_at, timeout=timeout)

    def app(self, text, timeout=5):
        self.app_at = self.shell.wait_log(text, self.app_at, log="app.log", timeout=timeout)

    def ext_lines(self):
        return [line for line in self.shell.log.read_text(errors="replace")[self.shell_at:].splitlines()
                if "[ringconvert]" in line]


@pytest.fixture
def marks(shell):
    marks = Marks(shell)
    yield marks
    # Leave no drag or ring behind for the next test.
    shell.input.button(False)
    shell.input.key("Shift_L", False)
    time.sleep(0.3)
    shell.call(APP_ID, "/io/github/ringconvert/RingConvert", APP_ID, "Hide")
    time.sleep(0.5)


def start_drag(shell, x, y):
    """Press in the drag source and drag to (x, y)."""
    i = shell.input
    i.move_to(300, 300, 4)
    time.sleep(0.2)
    i.button(True)
    time.sleep(0.1)
    i.move_to(330, 320, 6)  # past GTK's drag threshold
    time.sleep(0.2)
    i.move_to(x, y, 10)
    time.sleep(0.3)


def test_enabled_without_errors(shell):
    log = shell.log.read_text(errors="replace")
    assert "[ringconvert] enabled" in log
    bad = [line for line in log.splitlines()
           if ("ringconvert" in line or UUID in line) and
           any(word in line for word in ("CRITICAL", "JS ERROR", "WARNING", "Error"))]
    assert not bad, "\n".join(bad)
    info = shell.extension_call("GetExtensionInfo")[0]
    assert info["state"] == 1.0  # ENABLED
    assert not info.get("error")


def test_portals_stay_out_of_the_real_runtime_dir(shell):
    # The private bus starts its own document portal and gvfs, which mount FUSE
    # file systems in XDG_RUNTIME_DIR. In the user's /run/user/UID they once took
    # the real session's document portal down.
    assert shell.runtime != shell.real_runtime
    assert str(shell.env["XDG_RUNTIME_DIR"]) == str(shell.runtime)
    shell.wait_for(lambda: shell.has_owner("org.freedesktop.portal.Documents") or
                   not shell.has_owner("org.freedesktop.portal.Desktop"), "the document portal", 10)
    assert mounts_under(shell.real_runtime) == shell.real_mounts
    if shell.has_owner("org.freedesktop.portal.Documents"):
        assert str(shell.runtime / "doc") in mounts_under(shell.runtime)


def test_drag_shift_drop_converts(shell, marks):
    before = set(shell.image.parent.iterdir())
    start_drag(shell, 640, 400)
    marks.ext("drag begin (shift held: false)")
    shell.input.tap("Shift_L")
    marks.ext("call Show(640, 400)")
    marks.ext("ring 460x460 centred on (640, 400) at (410, 170) on monitor 0")
    marks.app("D-Bus Show(640, 400)")
    marks.app("set_files paths=")  # the ring read the dragged file while hovered
    shell.input.move_to(640, 400 - 130, 8)  # the top segment
    time.sleep(0.4)
    shell.input.button(False)
    marks.ext("after drag: button held false, over disc true")
    marks.app("→ ", timeout=15)
    new = set(shell.image.parent.iterdir()) - before
    assert len(new) == 1 and next(iter(new)).stem == "Photo"
    marks.ext("ring closed")
    assert not any("call Hide" in line for line in marks.ext_lines())


def test_shift_toggles_rings(shell, marks):
    start_drag(shell, 640, 400)
    shell.input.tap("Shift_L")
    marks.app("present_ring files=[] mode=convert")
    time.sleep(0.5)
    shell.input.tap("Shift_L")
    marks.ext("call ShowTools(640, 400)")
    marks.app("present_ring files=[] mode=tools")
    time.sleep(0.3)
    shell.input.tap("Shift_L")
    marks.ext("call Show(640, 400)")
    marks.app("present_ring files=[] mode=convert")


def test_release_outside_hides(shell, marks):
    start_drag(shell, 1000, 600)
    shell.input.tap("Shift_L")
    marks.ext("centred on (1000, 600)")
    shell.input.move_to(200, 200, 8)
    shell.input.button(False)
    marks.ext("after drag: button held false, over disc false")
    marks.ext("call Hide()")
    marks.app("dismiss")
    marks.ext("ring closed")


def test_escape_hides_during_drag(shell, marks):
    start_drag(shell, 640, 400)
    shell.input.tap("Shift_L")
    marks.ext("centred on (640, 400)")
    shell.input.tap("Escape")
    marks.ext("call Hide()")
    marks.app("dismiss")
    marks.ext("ring closed")


def test_shift_held_before_the_drag_is_ignored(shell, marks):
    shell.input.move_to(300, 300, 2)
    shell.input.key("Shift_L", True)
    time.sleep(0.2)
    start_drag(shell, 500, 500)
    marks.ext("drag begin (shift held: true)")
    time.sleep(0.5)
    shell.input.button(False)
    marks.ext("drag end")
    time.sleep(0.3)
    assert not any("call" in line for line in marks.ext_lines())


def test_shortcut_ring_centres_on_pointer_and_stays_on_screen(shell, marks):
    # `ringconvert --show` (the keyboard shortcut) near the bottom-right corner.
    shell.input.move_to(WIDTH - 20, HEIGHT - 10, 4)
    shell.spawn([sys.executable, "-m", "ringconvert.app", "--show"], "show.log")
    left, top = WIDTH - RING + SLACK, HEIGHT - RING + SLACK
    marks.ext(f"centred on ({WIDTH - 20}, {HEIGHT - 10}) at ({left}, {top}) on monitor 0", timeout=10)
    shell.input.tap("Escape")
    marks.ext("ring closed")


def test_second_monitor(shell, marks):
    x, y = WIDTH + SECOND[0] - 10, SECOND[1] - 10
    start_drag(shell, x, y)
    shell.input.tap("Shift_L")
    left, top = WIDTH + SECOND[0] - RING + SLACK, SECOND[1] - RING + SLACK
    marks.ext(f"centred on ({x}, {y}) at ({left}, {top}) on monitor 1")


def test_disable_removes_everything(shell, marks):
    assert shell.extension_call("DisableExtension") == (True,)
    marks.ext("disabled")
    start_drag(shell, 640, 400)
    shell.input.tap("Shift_L")
    time.sleep(0.8)
    shell.input.button(False)
    time.sleep(0.5)
    assert not marks.ext_lines()  # no drag tracking, no calls, no window handling
    assert shell.extension_call("EnableExtension") == (True,)
    marks.ext("enabled")
