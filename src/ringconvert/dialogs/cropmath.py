# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop rectangle editing, in image pixels. Pure Python so it can be tested.

A box is (x, y, w, h). Handles are "move", the edges "n", "s", "e", "w" and
the corners "nw", "ne", "sw", "se". `ratio` is width / height, or None.
"""

from __future__ import annotations

Box = tuple[float, float, float, float]

MIN_SIZE = 8.0


def full(width: int, height: int) -> Box:
    return 0.0, 0.0, float(width), float(height)


def fit_ratio(box: Box, ratio: float, width: int, height: int) -> Box:
    """Largest `ratio` box inside the image, centred on `box`'s centre
    (shifted back inside the image where needed)."""
    if width / height > ratio:
        h = float(height)
        w = h * ratio
    else:
        w = float(width)
        h = w / ratio
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    x = min(max(cx - w / 2, 0.0), width - w)
    y = min(max(cy - h / 2, 0.0), height - h)
    return x, y, w, h


def drag(box: Box, handle: str, dx: float, dy: float, width: int, height: int,
         ratio: float | None = None, min_size: float = MIN_SIZE) -> Box:
    """`box` after dragging `handle` by (dx, dy) image pixels."""
    x, y, w, h = box
    if handle == "move":
        x = min(max(x + dx, 0.0), width - w)
        y = min(max(y + dy, 0.0), height - h)
        return x, y, w, h
    if ratio:
        return _drag_ratio(box, handle, dx, dy, width, height, ratio, min_size)

    left, top, right, bottom = x, y, x + w, y + h
    if "w" in handle:
        left = min(max(left + dx, 0.0), right - min_size)
    if "e" in handle:
        right = max(min(right + dx, float(width)), left + min_size)
    if "n" in handle:
        top = min(max(top + dy, 0.0), bottom - min_size)
    if "s" in handle:
        bottom = max(min(bottom + dy, float(height)), top + min_size)
    return left, top, right - left, bottom - top


def _drag_ratio(box: Box, handle: str, dx: float, dy: float, width: int, height: int,
                ratio: float, min_size: float) -> Box:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    min_w = max(min_size, min_size * ratio)

    if len(handle) == 2:
        # Corner: the opposite corner stays put; the larger pull wins.
        ax = x + w if "w" in handle else x
        ay = y + h if "n" in handle else y
        cand_w = w + (-dx if "w" in handle else dx)
        cand_h = h + (-dy if "n" in handle else dy)
        new_w = max(cand_w, cand_h * ratio)
        room_x = ax if "w" in handle else width - ax
        room_y = ay if "n" in handle else height - ay
        new_w = min(max(new_w, min_w), room_x, room_y * ratio)
        new_h = new_w / ratio
        nx = ax - new_w if "w" in handle else ax
        ny = ay - new_h if "n" in handle else ay
        return nx, ny, new_w, new_h

    if handle in ("e", "w"):
        # Side: the opposite side stays put, height follows, centred vertically.
        ax = x + w if handle == "w" else x
        new_w = w + (-dx if handle == "w" else dx)
        room_x = ax if handle == "w" else width - ax
        room_y = 2 * min(cy, height - cy)
        new_w = min(max(new_w, min_w), room_x, room_y * ratio)
        new_h = new_w / ratio
        nx = ax - new_w if handle == "w" else ax
        return nx, cy - new_h / 2, new_w, new_h

    # "n" / "s": the opposite edge stays put, width follows, centred horizontally.
    ay = y + h if handle == "n" else y
    new_h = h + (-dy if handle == "n" else dy)
    room_y = ay if handle == "n" else height - ay
    room_x = 2 * min(cx, width - cx)
    new_h = min(max(new_h, min_w / ratio), room_y, room_x / ratio)
    new_w = new_h * ratio
    ny = ay - new_h if handle == "n" else ay
    return cx - new_w / 2, ny, new_w, new_h


def resize(box: Box, new_w: float | None, new_h: float | None, width: int, height: int,
           ratio: float | None = None) -> Box:
    """Set the size from the W/H fields, keeping the top-left corner where it
    fits (moving it back inside the image otherwise)."""
    x, y, w, h = box
    if ratio:
        if new_w is not None:
            new_h = new_w / ratio
        elif new_h is not None:
            new_w = new_h * ratio
    w = float(new_w) if new_w is not None else w
    h = float(new_h) if new_h is not None else h
    if ratio:
        scale = min(1.0, width / w, height / h)
        w, h = w * scale, h * scale
    w = min(max(w, 1.0), float(width))
    h = min(max(h, 1.0), float(height))
    x = min(x, width - w)
    y = min(y, height - h)
    return x, y, w, h


def pixels(box: Box) -> tuple[int, int, int, int]:
    """The box rounded to whole pixels for the actual crop."""
    x, y, w, h = box
    left, top = round(x), round(y)
    return left, top, max(1, round(x + w) - left), max(1, round(y + h) - top)
