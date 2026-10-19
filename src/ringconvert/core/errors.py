# SPDX-License-Identifier: GPL-3.0-or-later
"""Exception types shared by the core library."""


class RingConvertError(Exception):
    """A conversion or tool failed in a way worth showing to the user."""


class BackendMissing(RingConvertError):
    """A required external tool or Python module is not installed."""

    def __init__(self, backend: str, hint: str = ""):
        self.backend = backend
        self.hint = hint
        msg = f"{backend} is not installed"
        if hint:
            msg += f" ({hint})"
        super().__init__(msg)


class Unsupported(RingConvertError):
    """The requested conversion or tool does not apply to this file type."""
