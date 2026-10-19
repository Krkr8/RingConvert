# SPDX-License-Identifier: GPL-3.0-or-later
# shellcheck shell=bash
# Sourced by install.sh and uninstall.sh before they change anything.
#
# Test runs (RINGCONVERT_TESTING=1, RINGCONVERT_NO_START=1, which only tests
# use, or anything started under pytest, which exports PYTEST_CURRENT_TEST)
# must be sandboxed: a private HOME, data and config dirs outside the real
# home, keyfile GSettings and --user-only. Anything else means a test is about
# to edit the real session or system (that happened once: a test without any
# sandbox wiped the user's real install), so refuse. Test mode also implies
# RINGCONVERT_NO_START (no service, no live Shell extension changes, no D-Bus
# reload).

if [[ -n ${RINGCONVERT_TESTING:-}${RINGCONVERT_NO_START:-}${PYTEST_CURRENT_TEST:-} ]]; then
    RINGCONVERT_NO_START=1
    _real_home=$(getent passwd "$(id -u)" | cut -d: -f6)
    _real_home=$(realpath -m -- "${_real_home:-/nonexistent}")
    _inside_real_home() {
        local path
        path=$(realpath -m -- "$1")
        [[ $path == "$_real_home" || $path == "$_real_home"/* ]]
    }
    _why=""
    if _inside_real_home "$HOME"; then
        _why="HOME is your real home ($HOME)"
    elif _inside_real_home "${XDG_DATA_HOME:-$HOME/.local/share}"; then
        _why="the data dir is in your real home (${XDG_DATA_HOME:-$HOME/.local/share})"
    elif _inside_real_home "${XDG_CONFIG_HOME:-$HOME/.config}"; then
        _why="the config dir is in your real home (${XDG_CONFIG_HOME:-$HOME/.config})"
    elif [[ ${GSETTINGS_BACKEND:-} != keyfile ]]; then
        _why="GSettings would write to your real dconf (GSETTINGS_BACKEND isn't keyfile)"
    elif (( ! ${USER_ONLY:-0} )); then
        _why="a full run calls sudo dnf and pip (tests use --user-only)"
    fi
    if [[ -n $_why ]]; then
        echo "Refusing to run in test mode: $_why." >&2
        echo "Tests must use a private HOME, XDG dirs and GSETTINGS_BACKEND=keyfile." >&2
        exit 3
    fi
    unset -f _inside_real_home
    unset _real_home _why
fi
