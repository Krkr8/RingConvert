#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# RingConvert installer for Fedora 44 Workstation.
#
# Run as your normal user (NOT with sudo): it asks for your password only
# for the dnf step. Safe to re-run.
#
# Installs: system packages, Python deps, the ringconvert / ringconvert-cli
# commands, the desktop file + icon (needed for notifications), D-Bus
# activation and "Open With", autostart of the background service, the
# keyboard shortcut (Super+Shift+C, or RINGCONVERT_SHORTCUT="<Super><Alt>r"),
# the Files right-click menu (loads after `nautilus -q`) and the GNOME Shell
# extension (drag a file + Shift; loads at the next login).
# ./uninstall.sh removes all of it again.
#
#   ./install.sh              everything
#   ./install.sh --user-only  skip dnf and pip; just (re)install the desktop
#                             integration (no sudo needed)
# RINGCONVERT_TESTING=1 is test mode (see uninstall.sh and scripts/test-guard.sh).
set -euo pipefail

USER_ONLY=0
case ${1:-} in
    "") ;;
    --user-only) USER_ONLY=1 ;;
    *) echo "Usage: ./install.sh [--user-only]" >&2; exit 2 ;;
esac

cd "$(dirname "$(readlink -f "$0")")"
# shellcheck source=scripts/test-guard.sh
source scripts/test-guard.sh  # test runs must be sandboxed

if [[ $EUID -eq 0 ]]; then
    echo "Run ./install.sh as your normal user; it calls sudo itself when needed." >&2
    exit 1
fi

say() { printf '\n\033[1;38;5;202m==>\033[0m \033[1m%s\033[0m\n' "$*"; }

PACKAGES=(
    python3-pip python3-gobject gtk4 libadwaita
    python3-pillow python3-PyMuPDF python3-img2pdf python3-pytest
    ghostscript perl-Image-ExifTool pandoc-cli libavif-tools
    libreoffice-writer libreoffice-calc libreoffice-impress
    nautilus-python
)

if (( ! USER_ONLY )); then

say "Installing Fedora packages (sudo dnf install)"
sudo dnf install -y "${PACKAGES[@]}"

# ffmpeg: Fedora ships ffmpeg-free (no x264/x265). RPM Fusion's full build
# gives proper H.264 encoding (CRF-based compression, better MP4/MOV output).
# (Output captured first: with pipefail, `grep -q` quitting early can fail the pipe.)
rpmfusion_enabled() { local repos; repos=$(dnf repolist --enabled 2>/dev/null) || true; grep -q '^rpmfusion-free ' <<<"$repos"; }
if rpm -q ffmpeg >/dev/null 2>&1; then
    say "ffmpeg (RPM Fusion) already installed"
elif rpmfusion_enabled; then
    say "RPM Fusion is enabled: switch ffmpeg-free to the full ffmpeg build?"
    echo "    This runs: sudo dnf swap ffmpeg-free ffmpeg --allowerasing"
    read -r -p "    Swap now? [Y/n] " answer
    if [[ ! $answer =~ ^[Nn] ]]; then
        sudo dnf swap -y ffmpeg-free ffmpeg --allowerasing
    elif ! command -v ffmpeg >/dev/null; then
        sudo dnf install -y ffmpeg-free
    fi
else
    say "Installing ffmpeg-free (enable RPM Fusion for the full ffmpeg)"
    sudo dnf install -y ffmpeg-free
    cat <<'EOF'
    To get the full ffmpeg later:
      sudo dnf install \
        https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
      sudo dnf swap ffmpeg-free ffmpeg --allowerasing
    then re-run ./install.sh
EOF
fi

# Not packaged in Fedora: pdf2docx, python-docx, pillow-heif. Installing the
# project itself (editable) pulls them in and adds ~/.local/bin/ringconvert-cli.
say "Installing Python packages for your user (pip --user)"
python3 -m pip install --user --upgrade -e .

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo "    Note: ~/.local/bin is not on your PATH yet; open a new terminal or log in again."
fi

fi  # USER_ONLY

BIN="$HOME/.local/bin/ringconvert"
APP_ID=io.github.ringconvert.RingConvert
# Read once; `gsettings list-schemas | grep -q` can fail under pipefail.
SCHEMAS=$(gsettings list-schemas 2>/dev/null) || true
has_schema() { grep -qx "$1" <<<"$SCHEMAS"; }

if (( USER_ONLY )) && [[ ! -x $BIN ]]; then
    echo "    Note: $BIN isn't installed yet; run ./install.sh without --user-only." >&2
fi

say "Installing the desktop file, icon and D-Bus activation (~/.local/share)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
install -Dm644 data/$APP_ID.desktop "$DATA_HOME/applications/$APP_ID.desktop"
install -Dm644 data/icons/hicolor/scalable/apps/$APP_ID.svg \
    "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
install -Dm644 data/dbus-1/$APP_ID.service "$DATA_HOME/dbus-1/services/$APP_ID.service"
# The data files run "ringconvert" from PATH; point them at the installed command.
sed -i "s|^Exec=ringconvert|Exec=$BIN|" \
    "$DATA_HOME/applications/$APP_ID.desktop" "$DATA_HOME/dbus-1/services/$APP_ID.service"
gtk-update-icon-cache -qtf "$DATA_HOME/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true

say "Starting RingConvert in the background at login (~/.config/autostart)"
install -Dm644 data/autostart/$APP_ID.desktop "$CONFIG_HOME/autostart/$APP_ID.desktop"
sed -i "s|^Exec=ringconvert|Exec=$BIN|" "$CONFIG_HOME/autostart/$APP_ID.desktop"

say "Keyboard shortcut"
MEDIA_KEYS=org.gnome.settings-daemon.plugins.media-keys
KEY_PATH=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ringconvert/
KEY_SCHEMA="$MEDIA_KEYS.custom-keybinding:$KEY_PATH"
SHORTCUT="${RINGCONVERT_SHORTCUT:-<Super><Shift>c}"
if has_schema "$MEDIA_KEYS"; then
    # Append our path to the list without touching the user's other shortcuts.
    if [[ $(gsettings get "$MEDIA_KEYS" custom-keybindings) != *"'$KEY_PATH'"* ]]; then
        python3 scripts/gsettings-list.py "$MEDIA_KEYS" custom-keybindings add "$KEY_PATH"
    fi
    gsettings set "$KEY_SCHEMA" name "RingConvert"
    gsettings set "$KEY_SCHEMA" command "$BIN --show"
    # Keep a binding the user changed in Settings; set it only the first time.
    existing=$(gsettings get "$KEY_SCHEMA" binding)
    if [[ $existing == "''" || -n ${RINGCONVERT_SHORTCUT:-} ]]; then
        gsettings set "$KEY_SCHEMA" binding "$SHORTCUT"
    fi
    SHORTCUT=$(gsettings get "$KEY_SCHEMA" binding)
    SHORTCUT=${SHORTCUT//\'/}
    echo "    $SHORTCUT runs: $BIN --show"
    echo "    (change it in Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts)"
else
    echo "    GNOME's media-keys settings aren't available; add a shortcut for \"$BIN --show\" yourself."
    SHORTCUT=""
fi

say "Files right-click menu (~/.local/share/nautilus-python/extensions)"
NAUTILUS_EXT="$DATA_HOME/nautilus-python/extensions/ringconvert-nautilus.py"
install -Dm644 nautilus/ringconvert-nautilus.py "$NAUTILUS_EXT"
# Fallback import path, in case Files' Python doesn't see the pip --user install.
sed -i "s|@RINGCONVERT_SRC@|$PWD/src|" "$NAUTILUS_EXT"
if rpm -q nautilus-python >/dev/null 2>&1; then
    echo "    Installed. Restart Files to load it: nautilus -q (then open Files again)."
else
    echo "    Installed, but Files needs the nautilus-python package: sudo dnf install nautilus-python"
fi

say "GNOME Shell extension (drag a file, press Shift)"
EXT_UUID=ringconvert@ringconvert.github.io
EXT_DIR="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
EXT_NEW=1
[[ -f $EXT_DIR/metadata.json ]] && EXT_NEW=0
for file in metadata.json extension.js; do
    install -Dm644 "gnome-extension/$EXT_UUID/$file" "$EXT_DIR/$file"
done
if has_schema org.gnome.shell; then
    # Enable it: in enabled-extensions, out of disabled-extensions. Other
    # entries are kept as they are.
    if [[ $(gsettings get org.gnome.shell enabled-extensions) != *"'$EXT_UUID'"* ]]; then
        python3 scripts/gsettings-list.py org.gnome.shell enabled-extensions add "$EXT_UUID"
    fi
    python3 scripts/gsettings-list.py org.gnome.shell disabled-extensions remove "$EXT_UUID"
    if [[ $(gsettings get org.gnome.shell disable-user-extensions) == true ]]; then
        echo "    Note: user extensions are switched off (Extensions app → \"Use Extensions\")."
    fi
    if (( EXT_NEW )); then
        echo "    Installed. Log out and back in to load it (Wayland can't load new Shell code live)."
    else
        echo "    Updated. Log out and back in to load the new version."
    fi
else
    echo "    GNOME Shell settings aren't available; enable $EXT_UUID in the Extensions app."
fi

if [[ -z ${RINGCONVERT_NO_START:-} ]]; then
    say "Starting the background service"
    # Restart so an upgrade takes effect, then start it through D-Bus
    # activation (owned by the session, not this terminal).
    "$BIN" --quit >/dev/null 2>&1 || true
    busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus \
        ReloadConfig >/dev/null 2>&1 || true
    if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
            --method org.freedesktop.DBus.StartServiceByName $APP_ID 0 >/dev/null 2>&1; then
        echo "    Running. It also starts by itself at every login."
    else
        echo "    Couldn't start it now; it will start at your next login."
    fi
fi

if (( ! USER_ONLY )); then
    say "Backend check"
    "$HOME/.local/bin/ringconvert-cli" --check || true
fi

if [[ -n $SHORTCUT ]]; then
    SHORTCUT_TIP="${SHORTCUT/<Super><Shift>c/Super+Shift+C} toggles the ring at any time."
else
    SHORTCUT_TIP="Add a shortcut for \"$BIN --show\" in Settings to toggle the ring at any time."
fi

say "Done."
cat <<EOF
    - Log out and back in once, then drag a file and press Shift while dragging.
    - Restart Files (nautilus -q) for the right-click "Convert with RingConvert" menu.
    - $SHORTCUT_TIP
    - ./uninstall.sh removes everything again.
EOF
