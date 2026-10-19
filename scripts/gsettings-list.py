#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add or remove one entry of a GSettings string list, keeping the others
(used by install.sh / uninstall.sh for shortcuts and Shell extensions).

    scripts/gsettings-list.py SCHEMA[:PATH] KEY add|remove VALUE
"""

import ast
import subprocess
import sys


def main(schema: str, key: str, action: str, value: str) -> int:
    current = subprocess.run(["gsettings", "get", schema, key], check=True,
                             capture_output=True, text=True).stdout.strip()
    raw = current.removeprefix("@as ")
    items = ast.literal_eval(raw) if raw.strip() else []
    updated = [item for item in items if item != value]
    if action == "add":
        updated.append(value)
    elif action != "remove":
        sys.exit(f"unknown action {action!r}")
    if updated != items:
        subprocess.run(["gsettings", "set", schema, key, str(updated)], check=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
