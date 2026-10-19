# SPDX-License-Identifier: GPL-3.0-or-later
"""Image tools behind the CROP, EDIT, ADD BG, ANNOTATE and REDACT dialogs.

Each tool has a `render_*` function returning a Pillow image (for live
previews) and a function that writes the suffixed output file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RingConvertError
from .image import _upright, open_image, save_image
from .naming import Result, discard, output_path

# ---------------------------------------------------------------- helpers


def load(src: Path):
    """The upright first frame of an image, ready for editing."""
    img = _upright(open_image(src))
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
        img = img.copy()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    return img


def _write(src: Path, img, suffix: str, fmt: str | None = None, keep_metadata: bool = True) -> Result:
    result = Result()
    fmt = fmt or src.suffix.lower().lstrip(".") or "png"
    fmt = {"jpeg": "jpg", "tif": "tiff", "heif": "heic"}.get(fmt, fmt)
    dst = output_path(src, fmt, suffix, result)
    try:
        # Keep the ICC profile so colours don't shift; EXIF orientation was
        # already applied to the pixels, so drop EXIF unless asked to keep it.
        save_image(img, dst, fmt, quality=92, keep_metadata=keep_metadata)
    except BaseException:
        discard(dst)
        raise
    result.outputs.append(dst)
    return result


def parse_color(value: str) -> tuple[int, int, int, int]:
    from PIL import ImageColor

    try:
        rgba = ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise RingConvertError(f"Not a colour: {value!r}") from exc
    return rgba


# ---------------------------------------------------------------- crop


def ratio_value(ratio: str | None) -> float | None:
    """"16:9" -> 1.777..., "free"/"auto"/None -> None."""
    if not ratio or ratio.lower() in ("free", "auto"):
        return None
    try:
        w, h = ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError) as exc:
        raise RingConvertError(f"Bad aspect ratio {ratio!r}") from exc


def fit_ratio(width: int, height: int, ratio: float) -> tuple[int, int, int, int]:
    """Largest centred box of `ratio` inside width x height: (x, y, w, h)."""
    if width / height > ratio:
        h = height
        w = round(h * ratio)
    else:
        w = width
        h = round(w / ratio)
    return (width - w) // 2, (height - h) // 2, w, h


def render_crop(img, box: tuple[int, int, int, int]):
    x, y, w, h = (int(round(v)) for v in box)
    x, y = max(0, x), max(0, y)
    w, h = min(w, img.width - x), min(h, img.height - y)
    if w <= 0 or h <= 0:
        raise RingConvertError("The crop area is empty")
    return img.crop((x, y, x + w, y + h))


def crop(src: Path, box: tuple[int, int, int, int] | None = None, ratio: str | None = None) -> Result:
    """Crop to `box` (x, y, w, h in px), or the largest centred `ratio` box."""
    img = load(src)
    if box is None:
        r = ratio_value(ratio)
        box = fit_ratio(img.width, img.height, r) if r else (0, 0, img.width, img.height)
    return _write(src, render_crop(img, box), " Cropped")


# ---------------------------------------------------------------- edit


@dataclass
class EditParams:
    rotate: int = 0  # clockwise degrees, multiple of 90
    flip_h: bool = False
    flip_v: bool = False
    width: int | None = None
    height: int | None = None
    brightness: float = 1.0  # 1.0 = unchanged
    contrast: float = 1.0
    saturation: float = 1.0


def render_edit(img, p: EditParams):
    from PIL import Image, ImageEnhance

    if p.rotate % 360:
        if p.rotate % 90:
            raise RingConvertError("Rotation must be a multiple of 90°")
        img = img.rotate(-p.rotate, expand=True)
    if p.flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if p.flip_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if p.width or p.height:
        w = p.width or round(img.width * p.height / img.height)
        h = p.height or round(img.height * p.width / img.width)
        if w <= 0 or h <= 0:
            raise RingConvertError("Size must be positive")
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = img.convert("RGB")
    for enhancer, amount in ((ImageEnhance.Brightness, p.brightness),
                             (ImageEnhance.Contrast, p.contrast),
                             (ImageEnhance.Color, p.saturation)):
        if amount != 1.0:
            rgb = enhancer(rgb).enhance(amount)
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb


def edit(src: Path, params: EditParams) -> Result:
    return _write(src, render_edit(load(src), params), " Edited")


# ---------------------------------------------------------------- add background


@dataclass
class Background:
    """kind: "gradient" (colors + angle), "solid" (colors[0]) or "image" (path)."""

    kind: str = "gradient"
    colors: list[str] = field(default_factory=lambda: ["#ff6a3d", "#ffb347", "#8f7cff"])
    angle: float = 135.0  # degrees, CSS-style: 0 = towards top, 90 = towards right
    path: Path | None = None


# Swatches shown in the Add BG dialog: a row of gradients, a row of solids.
GRADIENTS = [
    ["#f9508a", "#ff9a3c", "#ffd447", "#8f7cff"], ["#ffffff", "#9cc9ff"],
    ["#ffe36e", "#ff8fb1", "#7fd6ff"], ["#fff1f4", "#f3b6d0"], ["#f7a8d8", "#b39cff"],
    ["#e2433f", "#1f5e4b"], ["#4f8cff", "#a6d8ff"], ["#2fd0e0", "#2f80ed"], ["#fbe3f0", "#cdb6ff"],
]
SOLIDS = ["#ffffff", "#f2c94c", "#8cc63f", "#2e9d5b", "#1f9d8a", "#36c2d9", "#2f6fed",
          "#1e2a44", "#7b4fd6"]


def _gradient(size: tuple[int, int], colors: list[str], angle: float):
    """Linear multi-stop gradient, computed at low res then smoothly upscaled."""
    from PIL import Image

    w, h = size
    small_w, small_h = max(2, min(w, 256)), max(2, min(h, 256))
    stops = [parse_color(c)[:3] for c in colors] or [(255, 255, 255)]
    if len(stops) == 1:
        return Image.new("RGB", size, stops[0])
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)
    # Project each pixel onto the gradient direction, normalised to 0..1.
    corners = [(0, 0), (small_w, 0), (0, small_h), (small_w, small_h)]
    proj = [cx * dx + cy * dy for cx, cy in corners]
    lo, hi = min(proj), max(proj)
    span = (hi - lo) or 1
    img = Image.new("RGB", (small_w, small_h))
    px = img.load()
    segments = len(stops) - 1
    for y in range(small_h):
        for x in range(small_w):
            t = ((x + 0.5) * dx + (y + 0.5) * dy - lo) / span
            pos = min(max(t, 0.0), 1.0) * segments
            i = min(int(pos), segments - 1)
            f = pos - i
            a, b = stops[i], stops[i + 1]
            px[x, y] = tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))
    return img.resize(size, Image.Resampling.BICUBIC)


def _background_canvas(size: tuple[int, int], bg: Background):
    from PIL import Image, ImageOps

    if bg.kind == "image":
        if not bg.path:
            raise RingConvertError("No background image chosen")
        return ImageOps.fit(load(Path(bg.path)).convert("RGB"), size, Image.Resampling.LANCZOS)
    if bg.kind == "solid":
        return Image.new("RGB", size, parse_color(bg.colors[0])[:3])
    return _gradient(size, bg.colors, bg.angle)


def _rounded_mask(size: tuple[int, int], radius: int):
    from PIL import Image, ImageDraw

    # Draw at 4x and downsample for anti-aliased corners.
    scale = 4
    big = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, big.width - 1, big.height - 1), radius=radius * scale, fill=255)
    return big.resize(size, Image.Resampling.LANCZOS)


def render_background(img, bg: Background, padding: int = 120, corners: int = 40,
                      shadow: int = 24, ratio: str | None = "auto", max_side: int | None = None):
    """Place `img` on a decorative background. `max_side` downsizes for previews."""
    from PIL import Image, ImageFilter

    if max_side and max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.LANCZOS)
        padding, corners, shadow = (round(v * scale) for v in (padding, corners, shadow))
    padding, corners, shadow = max(0, padding), max(0, corners), max(0, shadow)
    corners = min(corners, min(img.size) // 2)

    w, h = img.width + 2 * padding, img.height + 2 * padding
    r = ratio_value(ratio)
    if r:
        # Grow the shorter side so the canvas has the ratio; never crop.
        if w / h < r:
            w = round(h * r)
        else:
            h = round(w / r)
    canvas = _background_canvas((w, h), bg).convert("RGBA")
    x, y = (w - img.width) // 2, (h - img.height) // 2

    mask = _rounded_mask(img.size, corners)
    if img.mode == "RGBA":
        from PIL import ImageChops

        mask = ImageChops.multiply(mask, img.getchannel("A"))
    if shadow:
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        offset = round(shadow * 0.35)
        shade = Image.new("RGBA", img.size, (0, 0, 0, 110))
        layer.paste(shade, (x, y + offset), mask)
        layer = layer.filter(ImageFilter.GaussianBlur(shadow))
        canvas = Image.alpha_composite(canvas, layer)
    canvas.paste(img.convert("RGBA"), (x, y), mask)
    return canvas.convert("RGB")


def add_background(src: Path, bg: Background | None = None, padding: int = 120,
                   corners: int = 40, shadow: int = 24, ratio: str | None = "auto") -> Result:
    out = render_background(load(src), bg or Background(), padding, corners, shadow, ratio)
    return _write(src, out, " With Background", fmt="png", keep_metadata=False)


# ---------------------------------------------------------------- annotate

# A shape is a dict:
#   {"type": "arrow"|"rect"|"highlight"|"pen"|"text",
#    "points": [[x, y], ...]  (arrow/rect/highlight: 2 points, pen: many, text: 1),
#    "color": "#e8462a", "width": 6, "text": "...", "size": 32}
# Coordinates are in image pixels.


def _font(size: int):
    from PIL import ImageFont

    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/google-noto/NotoSans-Bold.ttf", "Cantarell-Bold.otf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_annotations(img, shapes: list[dict]):
    from PIL import Image, ImageDraw

    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for shape in shapes:
        kind = shape.get("type")
        color = parse_color(shape.get("color", "#e8462a"))
        width = max(1, int(shape.get("width", 6)))
        pts = [tuple(map(float, p)) for p in shape.get("points", [])]
        if kind in ("arrow", "rect", "highlight") and len(pts) < 2:
            raise RingConvertError(f"{kind} needs two points")
        if kind == "rect":
            (x0, y0), (x1, y1) = pts[:2]
            draw.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), outline=color, width=width)
        elif kind == "highlight":
            (x0, y0), (x1, y1) = pts[:2]
            draw.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), fill=color[:3] + (90,))
        elif kind == "arrow":
            _arrow(draw, pts[0], pts[1], color, width)
        elif kind == "pen":
            if len(pts) == 1:
                pts = pts * 2
            draw.line(pts, fill=color, width=width, joint="curve")
            r = width / 2
            for px, py in (pts[0], pts[-1]):
                draw.ellipse((px - r, py - r, px + r, py + r), fill=color)
        elif kind == "text":
            if not pts:
                raise RingConvertError("text needs a position")
            draw.multiline_text(pts[0], shape.get("text", ""), fill=color,
                                font=_font(int(shape.get("size", 32))))
        else:
            raise RingConvertError(f"Unknown annotation type {kind!r}")
    out = Image.alpha_composite(base, overlay)
    return out if img.mode == "RGBA" else out.convert("RGB")


def _arrow(draw, start, end, color, width):
    (x0, y0), (x1, y1) = start, end
    angle = math.atan2(y1 - y0, x1 - x0)
    head = max(12.0, width * 4)
    # Stop the shaft inside the head so its square end doesn't poke out.
    bx, by = x1 - math.cos(angle) * head * 0.8, y1 - math.sin(angle) * head * 0.8
    draw.line((x0, y0, bx, by), fill=color, width=width)
    spread = math.radians(28)
    left = (x1 - head * math.cos(angle - spread), y1 - head * math.sin(angle - spread))
    right = (x1 - head * math.cos(angle + spread), y1 - head * math.sin(angle + spread))
    draw.polygon([(x1, y1), left, right], fill=color)


def annotate(src: Path, shapes: list[dict]) -> Result:
    return _write(src, render_annotations(load(src), shapes), " Annotated")


# ---------------------------------------------------------------- redact


def render_redactions(img, rects: list[tuple[float, float, float, float]]):
    from PIL import ImageDraw

    out = img.copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h in rects:
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=(0, 0, 0, 255) if out.mode == "RGBA" else (0, 0, 0))
    return out


def redact(src: Path, rects: list[tuple[float, float, float, float]]) -> Result:
    """Black boxes over (x, y, w, h) px areas. All metadata is dropped too:
    an EXIF thumbnail would otherwise still show what was redacted."""
    if not rects:
        raise RingConvertError("Nothing to redact")
    return _write(src, render_redactions(load(src), rects), " Redacted", keep_metadata=False)
