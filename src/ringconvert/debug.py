# SPDX-License-Identifier: GPL-3.0-or-later
"""RINGCONVERT_DEBUG=1 traces input and state changes on stderr."""

from __future__ import annotations

import os
import sys
import time

ENABLED = bool(os.environ.get("RINGCONVERT_DEBUG"))
_start = time.monotonic()


def debug(message: str, *args) -> None:
    if ENABLED:
        text = message % args if args else message
        print(f"[ringconvert {time.monotonic() - _start:7.3f}] {text}", file=sys.stderr, flush=True)
