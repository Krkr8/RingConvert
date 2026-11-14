# SPDX-License-Identifier: GPL-3.0-or-later
# Sourced by install.sh and uninstall.sh: removes an install made under the IDs
# RingConvert used before 1.0.0. The keyboard shortcut, the Files menu script
# and the pip package kept their names, so the current scripts handle those.
#
# Needs DATA_HOME, CONFIG_HOME and has_schema from the caller. Honours
# RINGCONVERT_NO_START like the scripts themselves: the running session (old
# service, live extension) is left alone in test mode.

LEGACY_APP_ID=io.github.ringconvert.RingConvert
LEGACY_OBJECT_PATH=/io/github/ringconvert/RingConvert
LEGACY_EXT_UUID=ringconvert@ringconvert.github.io

remove_legacy_install() {
    local ext_dir="$DATA_HOME/gnome-shell/extensions/$LEGACY_EXT_UUID"
    local files=(
        "$CONFIG_HOME/autostart/$LEGACY_APP_ID.desktop"
        "$DATA_HOME/dbus-1/services/$LEGACY_APP_ID.service"
        "$DATA_HOME/applications/$LEGACY_APP_ID.desktop"
        "$DATA_HOME/icons/hicolor/scalable/apps/$LEGACY_APP_ID.svg"
    )
    local found=0 file
    for file in "${files[@]}"; do
        [[ -e $file ]] && found=1
    done
    [[ -e $ext_dir || -L $ext_dir ]] && found=1
    if has_schema org.gnome.shell &&
            [[ $(gsettings get org.gnome.shell enabled-extensions) == *"'$LEGACY_EXT_UUID'"* ]]; then
        found=1
    fi
    (( found )) || return 0

    echo "    Removing the pre-1.0 install ($LEGACY_APP_ID)"
    if [[ -z ${RINGCONVERT_NO_START:-} ]]; then
        gdbus call --session --dest $LEGACY_APP_ID --object-path $LEGACY_OBJECT_PATH \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 || true
        # Unload it from the running shell (never through a symlink: the shell
        # deletes the folder's contents).
        if [[ -d $ext_dir && ! -L $ext_dir ]]; then
            gnome-extensions uninstall $LEGACY_EXT_UUID >/dev/null 2>&1 ||
                gnome-extensions disable $LEGACY_EXT_UUID >/dev/null 2>&1 || true
        fi
    fi
    if has_schema org.gnome.shell; then
        python3 scripts/gsettings-list.py org.gnome.shell enabled-extensions remove $LEGACY_EXT_UUID
        python3 scripts/gsettings-list.py org.gnome.shell disabled-extensions remove $LEGACY_EXT_UUID
    fi
    if [[ -e $ext_dir || -L $ext_dir ]]; then
        rm -rf -- "$ext_dir"
    fi
    for file in "${files[@]}"; do
        rm -f -- "$file"
    done
}
