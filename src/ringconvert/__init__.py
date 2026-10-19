# SPDX-License-Identifier: GPL-3.0-or-later
"""RingConvert: drag a file, press Shift, drop it on a format."""

import os

# Overridable so tests can run a private instance next to a real one.
APP_ID = os.environ.get("RINGCONVERT_APP_ID", "io.github.ringconvert.RingConvert")
__version__ = "0.1.0"
