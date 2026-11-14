# SPDX-License-Identifier: GPL-3.0-or-later
"""A headless GNOME Shell for testing the Shell extension, isolated from the
user's session: its own D-Bus daemon, keyfile GSettings, XDG config/data/cache
dirs and Wayland socket. Input is injected through that shell's own
org.gnome.Mutter.RemoteDesktop, so the real cursor never moves.

    with HeadlessShell(workdir) as shell:
        shell.wait_log("[ringconvert] enabled")
        app = shell.spawn([sys.executable, "-m", "ringconvert.app", "--service"], "app.log")
        shell.input.move_to(640, 400); shell.input.button(True) …

Run it by hand to poke at the extension (Ctrl+C stops it):
    python3 tests/shell_harness.py
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UUID = "ringconvert@krkr8.github.io"
EXTENSION = ROOT / "gnome-extension" / UUID
WIDTH, HEIGHT = 1280, 800
BTN_LEFT = 0x110
KEYSYMS = {"Shift_L": 0xFFE1, "Escape": 0xFF1B}
REMOTE_DESKTOP = "org.gnome.Mutter.RemoteDesktop"


def available() -> str | None:
    """Why the harness can't run here, or None."""
    for program in ("gnome-shell", "dbus-daemon"):
        if not shutil.which(program):
            return f"needs {program}"
    return None


def mounts_under(path: Path) -> list[str]:
    """Mount points at or below `path` (from /proc/self/mountinfo)."""
    root = str(path)
    with open("/proc/self/mountinfo") as info:
        points = [line.split()[4].replace("\\040", " ") for line in info]
    return [p for p in points if p == root or p.startswith(root + "/")]


class HeadlessShell:
    def __init__(self, workdir: Path, extensions: tuple[str, ...] = (UUID,),
                 monitors: tuple[str, ...] = (f"{WIDTH}x{HEIGHT}",)):
        self.workdir = Path(workdir)
        self.display = f"rc-test-{os.getpid()}"
        self.log = self.workdir / "shell.log"
        self.extensions = extensions
        self.monitors = monitors
        self._procs: list[subprocess.Popen] = []
        self.runtime: Path | None = None
        self.bus: Gio.DBusConnection | None = None
        self.input: RemoteInput | None = None

    # ------------------------------------------------------------ lifecycle

    def __enter__(self) -> HeadlessShell:
        try:
            self.start()
        except BaseException:
            self.stop()
            raise
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        for sub in ("config/glib-2.0/settings", "data/gnome-shell/extensions", "cache"):
            (self.workdir / sub).mkdir(parents=True, exist_ok=True)
        target = self.workdir / "data/gnome-shell/extensions" / UUID
        if not target.exists():
            target.symlink_to(EXTENSION)
        enabled = "[" + ", ".join(f"'{e}'" for e in self.extensions) + "]" if self.extensions else "@as []"
        (self.workdir / "config/glib-2.0/settings/keyfile").write_text(
            f"[org/gnome/shell]\nenabled-extensions={enabled}\n"
            "welcome-dialog-last-shown-version='999'\n"
            # The hot corner's pressure barriers at (0, 0) eat parts of the
            # first relative motions out of the corner the pointer is pinned to.
            "[org/gnome/desktop/interface]\nenable-hot-corners=false\n")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                                 "XDG_SESSION_ID", "GNOME_SETUP_DISPLAY")}
        # A private runtime dir too: the portals, keyring and gvfs that the
        # private bus starts would otherwise share the user's /run/user/UID and
        # mount over it (the document portal at …/doc; stopping ours once
        # took the session's real document portal down). Short, because the
        # Wayland socket lives in it.
        self.runtime = Path(tempfile.mkdtemp(prefix="rc-run-"))
        self.runtime.chmod(0o700)
        self.env.update(XDG_CONFIG_HOME=str(self.workdir / "config"),
                        XDG_DATA_HOME=str(self.workdir / "data"),
                        XDG_CACHE_HOME=str(self.workdir / "cache"),
                        XDG_RUNTIME_DIR=str(self.runtime),
                        GSETTINGS_BACKEND="keyfile", RINGCONVERT_DEBUG="1",
                        NO_AT_BRIDGE="1", GTK_A11Y="none")

        daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True)
        self._procs.append(daemon)
        address = daemon.stdout.readline().strip()
        self.env["DBUS_SESSION_BUS_ADDRESS"] = address
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)

        self.spawn(["gnome-shell", "--headless", "--wayland", "--no-x11",
                    f"--wayland-display={self.display}",
                    *(arg for size in self.monitors for arg in ("--virtual-monitor", size))], self.log)
        self.wait_for(lambda: self.has_owner(REMOTE_DESKTOP) and self.has_owner("org.gnome.Shell"),
                      "the shell's D-Bus API", 30)
        time.sleep(1)  # start-up animation
        # The shell starts in the overview; windows should be on the desktop.
        self.call("org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties", "Set",
                  ("org.gnome.Shell", "OverviewActive", GLib.Variant("b", False)), "(ssv)")
        self.input = RemoteInput(self.bus)
        # Clients of the nested shell.
        self.client_env = dict(self.env, WAYLAND_DISPLAY=self.display, GDK_BACKEND="wayland",
                               PYTHONPATH=str(ROOT / "src"))

    def stop(self) -> None:
        if self.input:
            self.input.close()
            self.input = None
        # Clients first, then the shell, then the bus.
        for proc in reversed(self._procs):
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self._procs.clear()
        self._sweep()
        if self.bus:
            try:
                self.bus.close_sync(None)
            except GLib.Error:
                pass  # the daemon is gone already
            self.bus = None
        if self.runtime:
            # Our document portal's FUSE mount may outlive it; never recurse into one.
            for point in sorted(mounts_under(self.runtime), reverse=True):
                subprocess.run(["fusermount3", "-u", "-z", point], capture_output=True)
            if not mounts_under(self.runtime):
                shutil.rmtree(self.runtime, ignore_errors=True)
            self.runtime = None

    def _sweep(self) -> None:
        """Stop anything the private bus activated (portals, gvfs, goa…) that outlived it."""
        marker = f"XDG_CONFIG_HOME={self.workdir / 'config'}".encode()
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if marker in (proc / "environ").read_bytes().split(b"\0"):
                    os.kill(int(proc.name), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    def spawn(self, argv: list[str], log: Path | str, env: dict | None = None) -> subprocess.Popen:
        """Start a process in the nested session (a client unless it's the shell)."""
        log = self.workdir / log
        base = self.env if argv[0] == "gnome-shell" else self.client_env
        with open(log, "a") as out:
            proc = subprocess.Popen(argv, env=dict(base, **(env or {})), cwd=ROOT,
                                    stdout=out, stderr=subprocess.STDOUT)
        self._procs.append(proc)
        return proc

    # ------------------------------------------------------------ queries

    def has_owner(self, name: str) -> bool:
        reply = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                   "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (name,)),
                                   None, Gio.DBusCallFlags.NONE, 2000, None)
        return reply.unpack()[0]

    def call(self, dest, path, interface, method, params=None, signature=None, timeout=5000):
        if params is not None and not isinstance(params, GLib.Variant):
            params = GLib.Variant(signature, params)
        return self.bus.call_sync(dest, path, interface, method, params, None,
                                  Gio.DBusCallFlags.NONE, timeout, None).unpack()

    def extension_call(self, method: str, uuid: str = UUID):
        return self.call("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
                         method, (uuid,), "(s)")

    @staticmethod
    def wait_for(predicate, what: str, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for {what}")
            time.sleep(0.05)

    def wait_log(self, text: str, after: int = 0, log: Path | str | None = None,
                 timeout: float = 5) -> int:
        """Wait until `text` appears in a log past offset `after`; return the offset past it."""
        path = self.workdir / log if log else self.log
        found = []

        def seen() -> bool:
            content = path.read_text(errors="replace") if path.exists() else ""
            index = content.find(text, after)
            if index >= 0:
                found.append(index + len(text))
            return bool(found)

        try:
            self.wait_for(seen, repr(text), timeout)
        except TimeoutError:
            content = path.read_text(errors="replace") if path.exists() else "(no log)"
            raise TimeoutError(f"{text!r} not in {path.name} after offset {after}:\n"
                               f"{content[after:][-6000:]}") from None
        return found[0]

    def log_size(self, log: Path | str | None = None) -> int:
        path = self.workdir / log if log else self.log
        return len(path.read_text(errors="replace")) if path.exists() else 0


class RemoteInput:
    """Pointer and keyboard of the headless shell via its RemoteDesktop session."""

    def __init__(self, bus: Gio.DBusConnection):
        self.bus = bus
        (self.path,) = self._call("/org/gnome/Mutter/RemoteDesktop", REMOTE_DESKTOP, "CreateSession")
        self._session("Start")
        self.x = self.y = 0.0
        self._keys: set[str] = set()  # keysyms held down
        # No absolute motion without a screencast stream: pin the pointer in the
        # top-left corner, then track it ourselves.
        self._session("NotifyPointerMotionRelative", (-100000, -100000), "(dd)")
        time.sleep(0.1)

    def _call(self, path, interface, method, params=None, signature=None):
        if params is not None:
            params = GLib.Variant(signature, params)
        return self.bus.call_sync(REMOTE_DESKTOP, path, interface, method, params, None,
                                  Gio.DBusCallFlags.NONE, 5000, None).unpack()

    def _session(self, method, params=None, signature=None):
        return self._call(self.path, REMOTE_DESKTOP + ".Session", method, params, signature)

    def move_to(self, x: float, y: float, steps: int = 1, delay: float = 0.02) -> None:
        dx, dy = (x - self.x) / steps, (y - self.y) / steps
        for _ in range(steps):
            self._session("NotifyPointerMotionRelative", (dx, dy), "(dd)")
            time.sleep(delay)
        self.x, self.y = x, y

    def button(self, pressed: bool, button: int = BTN_LEFT) -> None:
        self._session("NotifyPointerButton", (button, pressed), "(ib)")

    def key(self, name: str, pressed: bool) -> None:
        # Mutter 51 refuses to release a key that isn't down ("Invalid key event").
        if not pressed and name not in self._keys:
            return
        self._session("NotifyKeyboardKeysym", (KEYSYMS[name], pressed), "(ub)")
        (self._keys.add if pressed else self._keys.discard)(name)

    def tap(self, name: str, hold: float = 0.15) -> None:
        self.key(name, True)
        time.sleep(hold)
        self.key(name, False)

    def close(self) -> None:
        try:
            self._session("Stop")
        except GLib.Error:
            pass


if __name__ == "__main__":
    reason = available()
    if reason:
        sys.exit(reason)
    with tempfile.TemporaryDirectory(prefix="ringconvert-shell-") as tmp, HeadlessShell(Path(tmp)) as shell:
        # Clients need all three: the socket lives in the private runtime dir.
        print(f"headless shell up: XDG_RUNTIME_DIR={shell.runtime} WAYLAND_DISPLAY={shell.display} "
              f"DBUS_SESSION_BUS_ADDRESS={shell.env['DBUS_SESSION_BUS_ADDRESS']}\nlog: {shell.log}",
              flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
