# SPDX-License-Identifier: GPL-3.0-or-later
"""The resident service over D-Bus, run as a real process under a private
app ID (so a running RingConvert isn't touched). Needs a session bus and a
display; skips otherwise."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

Gio = pytest.importorskip("gi.repository.Gio")
GLib = pytest.importorskip("gi.repository.GLib")
Gtk = pytest.importorskip("gi.repository.Gtk")

ROOT = Path(__file__).resolve().parent.parent
INTERFACE = "io.github.krkr8.RingConvert"

try:
    BUS = Gio.bus_get_sync(Gio.BusType.SESSION, None)
except GLib.Error:
    BUS = None
pytestmark = pytest.mark.skipif(BUS is None or not Gtk.init_check(), reason="needs a session bus and display")


@pytest.fixture(params=["--service"])
def service(request, tmp_path):
    app_id = f"io.github.krkr8.Test{os.getpid()}"
    env = dict(os.environ, RINGCONVERT_APP_ID=app_id, RINGCONVERT_DEBUG="1",
               PYTHONPATH=str(ROOT / "src"))
    log = tmp_path / "service.log"

    def launch(*args, wait=True):
        with open(log, "a") as out:
            proc = subprocess.Popen([sys.executable, "-m", "ringconvert.app", *args],
                                    env=env, stdout=out, stderr=out)
        if wait:
            proc.wait(timeout=10)
        return proc

    proc = launch(request.param, wait=False)
    path = "/" + app_id.replace(".", "/")
    deadline = time.monotonic() + 10
    while not _has_owner(app_id):
        if time.monotonic() > deadline or proc.poll() is not None:
            proc.kill()
            pytest.fail("service didn't start:\n" + log.read_text())
        time.sleep(0.05)

    def call(method, *args, interface=INTERFACE, signature="(ii)"):
        params = GLib.Variant(signature, args) if args else None
        return BUS.call_sync(app_id, path, interface, method, params, None,
                             Gio.DBusCallFlags.NONE, 5000, None)

    def wait_log(text, after=0):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            content = log.read_text()
            if text in content[after:]:
                return content.index(text, after) + len(text)
            time.sleep(0.05)
        pytest.fail(f"{text!r} not logged:\n{log.read_text()}")

    yield type("Service", (), {"proc": proc, "call": staticmethod(call), "launch": staticmethod(launch),
                               "log": log, "wait_log": staticmethod(wait_log), "app_id": app_id})
    if proc.poll() is None:
        launch("--quit")
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _has_owner(name):
    reply = BUS.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", (name,)), None,
                          Gio.DBusCallFlags.NONE, 2000, None)
    return reply.unpack()[0]


def test_service_starts_without_a_window(service):
    time.sleep(0.5)
    assert service.proc.poll() is None  # stays resident
    assert "present_ring" not in service.log.read_text()


def test_show_showtools_hide(service):
    service.call("Show", 100, 200)
    pos = service.wait_log("present_ring files=[] mode=convert")
    service.call("ShowTools", 5, 6)
    pos = service.wait_log("present_ring files=[] mode=tools", pos)
    service.call("Hide", signature=None)
    pos = service.wait_log("dismiss", pos)
    service.wait_log("after dismiss", pos)
    time.sleep(0.3)
    assert service.proc.poll() is None  # closing the ring doesn't stop the service


def test_show_option_toggles(service):
    service.launch("--show")
    pos = service.wait_log("present_ring")
    time.sleep(0.4)  # let the appear animation start
    service.launch("--show")
    service.wait_log("dismiss", pos)


def test_actions_and_quit(service):
    reply = service.call("List", interface="org.gtk.Actions", signature=None)
    assert {"show-in-files", "quit"} <= set(reply.unpack()[0])
    service.launch("--quit")
    service.proc.wait(timeout=5)
    assert service.proc.returncode == 0


@pytest.mark.parametrize("service", ["--gapplication-service"], indirect=True)
def test_dbus_activation_stays_resident_and_activates(service):
    # How GNOME starts it for the app grid, "Open With" and notification buttons.
    time.sleep(0.5)
    assert service.proc.poll() is None
    service.call("Activate", {}, interface="org.freedesktop.Application", signature="(a{sv})")
    service.wait_log("present_ring")
