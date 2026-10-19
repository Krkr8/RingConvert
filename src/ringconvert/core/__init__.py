# SPDX-License-Identifier: GPL-3.0-or-later
"""RingConvert core: file conversions and tools, usable without any GUI."""

from .convert import convert, convert_many
from .detect import FileInfo, common_targets, common_tools, detect, label, targets, tools
from .errors import BackendMissing, RingConvertError, Unsupported
from .naming import Result

__all__ = [
    "BackendMissing", "FileInfo", "Result", "RingConvertError", "Unsupported",
    "common_targets", "common_tools", "convert", "convert_many", "detect", "label", "targets",
    "tools",
]
