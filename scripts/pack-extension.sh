#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Builds the Shell extension zip for extensions.gnome.org and GitHub releases:
#   dist/ringconvert@krkr8.github.io.shell-extension.zip
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
UUID=ringconvert@krkr8.github.io
mkdir -p dist
gnome-extensions pack "gnome-extension/$UUID" --extra-source=../../LICENSE \
    --out-dir=dist --force
echo "dist/$UUID.shell-extension.zip"
