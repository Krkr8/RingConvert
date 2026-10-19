# SPDX-License-Identifier: GPL-3.0-or-later
"""install.sh / uninstall.sh round trip in private XDG dirs with keyfile
GSettings (RINGCONVERT_NO_START=1: the running session is never touched).
Also keeps the desktop file's "Open With" MIME types in sync with detect."""

from __future__ import annotations

import configparser
import os
import pwd
import py_compile
import shutil
import subprocess
from pathlib import Path

import pytest

from ringconvert.core.detect import _MIME_FORMATS

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.ringconvert.RingConvert"
UUID = "ringconvert@ringconvert.github.io"
KEY_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ringconvert/"
OTHER_KEY = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"

needs_gsettings = pytest.mark.skipif(
    not shutil.which("gsettings") or "org.gnome.shell" not in subprocess.run(
        ["gsettings", "list-schemas"], capture_output=True, text=True).stdout,
    reason="needs gsettings with the GNOME Shell schemas")


def test_open_with_lists_every_detected_type():
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(ROOT / f"data/{APP_ID}.desktop")
    entry = desktop["Desktop Entry"]
    assert set(filter(None, entry["MimeType"].split(";"))) == set(_MIME_FORMATS)
    assert "%U" in entry["Exec"]


@pytest.fixture
def home(tmp_path):
    config, data, user, fake = (tmp_path / d for d in ("config", "data", "home", "bin"))
    (config / "glib-2.0/settings").mkdir(parents=True)
    for d in (data, user, fake):
        d.mkdir()
    (config / "glib-2.0/settings/keyfile").write_text(
        "[org/gnome/shell]\n"
        "enabled-extensions=['ding@rastersoft.com']\n"
        "disabled-extensions=['old@example.com']\n"
        "[org/gnome/settings-daemon/plugins/media-keys]\n"
        f"custom-keybindings=['{OTHER_KEY}']\n")
    # Even a script that misreads its options and runs a full (un)install
    # must not reach the system or the real session: a private HOME (pip
    # --user), failing stand-ins for sudo & co., and no session bus.
    for name in ("sudo", "dnf", "gnome-extensions", "busctl", "gdbus"):
        (fake / name).write_text('#!/bin/sh\necho "fake $0 $*" >&2\nexit 1\n')
        (fake / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "GSETTINGS_SCHEMA_DIR"}
    env.update(HOME=str(user), PATH=f"{fake}:{env['PATH']}", XDG_CONFIG_HOME=str(config),
               XDG_DATA_HOME=str(data), GSETTINGS_BACKEND="keyfile", GIO_USE_VFS="local",
               DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent", PIP_NO_INDEX="1",
               RINGCONVERT_TESTING="1")
    env.pop("RINGCONVERT_NO_START", None)

    def call(*argv, **changes):
        """Run with the sandbox env; `changes` override variables (None unsets)."""
        run_env = dict(env)
        for key, value in changes.items():
            if value is None:
                run_env.pop(key, None)
            else:
                run_env[key] = value
        return subprocess.run(argv, cwd=ROOT, env=run_env, capture_output=True, text=True)

    def run(*argv):
        proc = call(*argv)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc.stdout

    def get(schema, key):
        return run("gsettings", "get", schema, key).strip()

    return type("Home", (), {"config": config, "data": data, "user": user, "call": staticmethod(call),
                             "run": staticmethod(run), "get": staticmethod(get)})


OURS = [
    "data/applications/io.github.ringconvert.RingConvert.desktop",
    "data/icons/hicolor/scalable/apps/io.github.ringconvert.RingConvert.svg",
    "data/dbus-1/services/io.github.ringconvert.RingConvert.service",
    "config/autostart/io.github.ringconvert.RingConvert.desktop",
    "data/nautilus-python/extensions/ringconvert-nautilus.py",
    f"data/gnome-shell/extensions/{UUID}/extension.js",
    f"data/gnome-shell/extensions/{UUID}/metadata.json",
]


def tree(base: Path) -> set[str]:
    return {str(p.relative_to(base)) + ("/" if p.is_dir() else "") for p in base.rglob("*")}


@needs_gsettings
def test_install_then_uninstall_leaves_only_the_users_settings(home):
    shell, keys = "org.gnome.shell", "org.gnome.settings-daemon.plugins.media-keys"
    ours = f"{keys}.custom-keybinding:{KEY_PATH}"
    base = home.config.parent
    before = tree(base)
    for _ in range(2):  # re-running is safe
        home.run("./install.sh", "--user-only")
    for rel in OURS:
        assert (base / rel).is_file(), rel
    # Files compiles the extension when it loads it.
    py_compile.compile(str(base / OURS[4]), doraise=True)
    assert home.get(shell, "enabled-extensions") == f"['ding@rastersoft.com', '{UUID}']"
    assert home.get(keys, "custom-keybindings") == f"['{OTHER_KEY}', '{KEY_PATH}']"
    assert home.get(ours, "binding") == "'<Super><Shift>c'"
    assert "--show" in home.get(ours, "command")
    nautilus = (base / OURS[4]).read_text()
    assert "@RINGCONVERT_SRC@" not in nautilus and str(ROOT / "src") in nautilus
    desktop = (base / OURS[0]).read_text()
    assert f"Exec={home.user}/.local/bin/ringconvert %U" in desktop

    home.run("gsettings", "set", ours, "enable-in-lockscreen", "true")  # as Settings may
    out = home.run("./uninstall.sh", "--user-only")
    assert "removed" in out
    # Not a file or folder more than before: caches and empty folders go too.
    assert tree(base) == before
    assert home.get(shell, "enabled-extensions") == "['ding@rastersoft.com']"
    assert home.get(shell, "disabled-extensions") == "['old@example.com']"
    assert home.get(keys, "custom-keybindings") == f"['{OTHER_KEY}']"
    assert home.get(ours, "binding") == "''"
    assert home.get(ours, "command") == "''"
    assert home.get(ours, "enable-in-lockscreen") == "false"
    home.run("./uninstall.sh", "--user-only")  # nothing left to remove: still fine


@needs_gsettings
def test_uninstall_keeps_other_apps(home):
    viewer = home.data / "applications/viewer.desktop"
    viewer.parent.mkdir(parents=True)
    viewer.write_text("[Desktop Entry]\nType=Application\nName=Viewer\nExec=cat %f\nMimeType=image/png;\n")
    other = home.config / "autostart/other.desktop"
    other.parent.mkdir(parents=True)
    other.write_text("[Desktop Entry]\nType=Application\nName=Other\nExec=other\n")
    home.run("./install.sh", "--user-only")
    home.run("./uninstall.sh", "--user-only")
    assert viewer.is_file() and other.is_file()
    # The "Open With" index stays for the other app, without us.
    index = (home.data / "applications/mimeinfo.cache").read_text()
    assert "viewer.desktop" in index and APP_ID not in index


@needs_gsettings
def test_install_reenables_a_disabled_extension(home):
    home.run("gsettings", "set", "org.gnome.shell", "disabled-extensions", f"['{UUID}', 'old@example.com']")
    home.run("./install.sh", "--user-only")
    assert home.get("org.gnome.shell", "disabled-extensions") == "['old@example.com']"


REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
# Inside the real home but unused: if the guard ever failed, a run would only
# create files here (removed again below).
DECOY = REAL_HOME / ".cache" / f"ringconvert-guard-test-{os.getpid()}"


@pytest.mark.parametrize("script", ["./install.sh", "./uninstall.sh"])
@pytest.mark.parametrize("changes, args, why", [
    ({"HOME": str(REAL_HOME)}, ["--user-only"], "HOME is your real home"),
    # The accident: a test with no sandbox variables at all. pytest's own
    # PYTEST_CURRENT_TEST (inherited) still puts the scripts in test mode.
    ({"HOME": str(REAL_HOME), "RINGCONVERT_TESTING": None}, ["--user-only"], "HOME is your real home"),
    ({"XDG_DATA_HOME": str(DECOY / "data")}, ["--user-only"], "the data dir is in your real home"),
    ({"XDG_CONFIG_HOME": str(DECOY / "config")}, ["--user-only"], "the config dir is in your real home"),
    ({"GSETTINGS_BACKEND": None}, ["--user-only"], "GSettings would write to your real dconf"),
    ({}, [], "a full run calls sudo dnf and pip"),
])
def test_test_mode_refuses_without_a_sandbox(home, script, changes, args, why):
    assert os.environ.get("PYTEST_CURRENT_TEST")  # what the second case relies on
    before = tree(home.config.parent)
    try:
        proc = home.call(script, *args, **changes)
        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert f"Refusing to run in test mode: {why}" in proc.stderr
        assert proc.stdout == ""  # stopped before its first step
        assert not DECOY.exists()
        assert tree(home.config.parent) == before
    finally:
        shutil.rmtree(DECOY, ignore_errors=True)


@pytest.mark.parametrize("script", ["./install.sh", "./uninstall.sh"])
def test_scripts_refuse_unknown_options(home, script):
    proc = home.call(script, "--useronly")  # a typo must not mean "everything"
    assert proc.returncode == 2 and "Usage:" in proc.stderr
