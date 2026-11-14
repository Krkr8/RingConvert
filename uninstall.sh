#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# RingConvert uninstaller: removes everything install.sh set up for your user.
#
# Run as your normal user (no sudo). Safe to re-run.
#
# Removes: the background service (stopped), autostart entry, D-Bus activation,
# desktop file ("Open With") and icon, the keyboard shortcut, the Files
# right-click menu, the GNOME Shell extension (unloaded right away), the
# ringconvert / ringconvert-cli commands (pip --user package), and the folders
# install.sh created once they are empty.
# Keeps: Fedora packages (other apps use them), the Python libraries pip pulled
# in (pdf2docx, python-docx, pillow-heif; the end message says how to remove
# them), and every file you converted.
#
#   ./uninstall.sh              everything
#   ./uninstall.sh --user-only  keep the pip package; remove only the desktop
#                               integration (mirrors install.sh --user-only)
# RINGCONVERT_TESTING=1 (or RINGCONVERT_NO_START=1) is test mode: the running
# session is left alone (no service stop, no live extension removal, no D-Bus
# reload), and the script refuses to run at all without a private HOME, XDG
# dirs and keyfile GSettings (scripts/test-guard.sh).
set -euo pipefail

USER_ONLY=0
case ${1:-} in
    "") ;;
    --user-only) USER_ONLY=1 ;;
    *) echo "Usage: ./uninstall.sh [--user-only]" >&2; exit 2 ;;
esac

cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=scripts/test-guard.sh
source scripts/test-guard.sh  # test runs must be sandboxed

if [[ $EUID -eq 0 ]]; then
    echo "Run ./uninstall.sh as your normal user." >&2
    exit 1
fi

say() { printf '\n\033[1;38;5;202m==>\033[0m \033[1m%s\033[0m\n' "$*"; }

APP_ID=io.github.krkr8.RingConvert
OBJECT_PATH=/io/github/krkr8/RingConvert
EXT_UUID=ringconvert@krkr8.github.io
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
# Read once; `gsettings list-schemas | grep -q` can fail under pipefail.
SCHEMAS=$(gsettings list-schemas 2>/dev/null) || true
has_schema() { grep -qx "$1" <<<"$SCHEMAS"; }

removed() { [[ -e $1 ]] && rm -f -- "$1" && echo "    removed $1" || true; }

# Remove DIR, then its parents below TOP, as long as they are empty.
prune() {
    local dir=$1 top=$2
    while [[ $dir == "$top"/* ]]; do
        if [[ -d $dir ]]; then
            rmdir -- "$dir" 2>/dev/null || return 0
        fi
        dir=$(dirname -- "$dir")
    done
}

if [[ -z ${RINGCONVERT_NO_START:-} ]]; then
    say "Stopping the background service"
    if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
            --method org.freedesktop.DBus.NameHasOwner $APP_ID 2>/dev/null | grep -q true; then
        gdbus call --session --dest $APP_ID --object-path $OBJECT_PATH \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 || true
        echo "    Stopped."
    else
        echo "    Not running."
    fi
fi

# shellcheck source=scripts/legacy-ids.sh
source scripts/legacy-ids.sh
remove_legacy_install  # an install from before 1.0.0, under the old IDs

say "GNOME Shell extension"
EXT_DIR="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
if [[ -z ${RINGCONVERT_NO_START:-} ]]; then
    # Let the running shell switch it off and unload it, so it's gone at once
    # (`disable` alone keeps it listed until the next login). The shell then
    # deletes the folder's contents, so never through a symlink to a checkout.
    if [[ ! -L $EXT_DIR ]] && gnome-extensions uninstall $EXT_UUID >/dev/null 2>&1; then
        echo "    switched off and removed from the running shell"
    else
        gnome-extensions disable $EXT_UUID >/dev/null 2>&1 || true
    fi
fi
if has_schema org.gnome.shell; then
    python3 scripts/gsettings-list.py org.gnome.shell enabled-extensions remove $EXT_UUID
    python3 scripts/gsettings-list.py org.gnome.shell disabled-extensions remove $EXT_UUID
fi
if [[ -e $EXT_DIR || -L $EXT_DIR ]]; then
    rm -rf -- "$EXT_DIR"
    echo "    removed $EXT_DIR"
fi
prune "$DATA_HOME/gnome-shell/extensions" "$DATA_HOME"

say "Files right-click menu"
NAUTILUS_DIR="$DATA_HOME/nautilus-python/extensions"
removed "$NAUTILUS_DIR/ringconvert-nautilus.py"
for pyc in "$NAUTILUS_DIR"/__pycache__/ringconvert-nautilus.*.pyc; do
    removed "$pyc"
done
prune "$NAUTILUS_DIR/__pycache__" "$DATA_HOME"

say "Keyboard shortcut"
MEDIA_KEYS=org.gnome.settings-daemon.plugins.media-keys
KEY_PATH=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ringconvert/
KEY_SCHEMA="$MEDIA_KEYS.custom-keybinding:$KEY_PATH"
if has_schema "$MEDIA_KEYS"; then
    python3 scripts/gsettings-list.py "$MEDIA_KEYS" custom-keybindings remove "$KEY_PATH"
    # Every key of the entry: name, command, binding and any Settings added.
    while read -r key; do
        gsettings reset "$KEY_SCHEMA" "$key"
    done < <(gsettings list-keys "$KEY_SCHEMA")
    echo "    removed"
fi

say "Autostart, D-Bus activation, desktop file and icon"
removed "$CONFIG_HOME/autostart/$APP_ID.desktop"
removed "$DATA_HOME/dbus-1/services/$APP_ID.service"
removed "$DATA_HOME/applications/$APP_ID.desktop"
removed "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
gtk-update-icon-cache -qtf "$DATA_HOME/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true
# install.sh's "Open With" index, if no other app is left in it.
MIME_CACHE="$DATA_HOME/applications/mimeinfo.cache"
if [[ -f $MIME_CACHE && $(<"$MIME_CACHE") == "[MIME Cache]" ]]; then
    rm -f -- "$MIME_CACHE"
fi
prune "$CONFIG_HOME/autostart" "$CONFIG_HOME"
for dir in dbus-1/services applications icons/hicolor/scalable/apps; do
    prune "$DATA_HOME/$dir" "$DATA_HOME"
done
if [[ -z ${RINGCONVERT_NO_START:-} ]]; then
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus \
        ReloadConfig >/dev/null 2>&1 || true
fi

if (( ! USER_ONLY )); then
    say "The ringconvert commands (pip --user)"
    if python3 -m pip show ringconvert >/dev/null 2>&1; then
        python3 -m pip uninstall -y ringconvert
    else
        echo "    Not installed."
    fi
fi

say "Done."
cat <<'EOF'
    - Restart Files (nautilus -q) so its menu forgets the RingConvert entry.
    - The Shell extension is already off. If the Extensions app still lists it, it's gone after
      your next login.
    - Left in place: Fedora packages and the Python libraries pip installed. To remove those too:
        python3 -m pip uninstall pdf2docx python-docx pillow-heif
EOF
