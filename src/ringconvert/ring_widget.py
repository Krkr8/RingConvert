# SPDX-License-Identifier: GPL-3.0-or-later
"""The radial menu: geometry, hit-testing, Cairo drawing and animation.

`RingPainter` is plain Cairo/Pango so it can be rendered off-screen (see
`render_png`, used for the README pictures and visual checks); `RingWidget` is the
Gtk.DrawingArea that animates it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402

RGBA = tuple[float, float, float, float]


def hex_rgba(value: str, alpha: float = 1.0) -> RGBA:
    value = value.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255, alpha)


def mix(a: RGBA, b: RGBA, t: float) -> RGBA:
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


@dataclass(frozen=True)
class Palette:
    disc_top: RGBA
    disc_bottom: RGBA
    disc_border: RGBA
    shadow: RGBA
    segment_top: RGBA
    segment_bottom: RGBA
    segment_highlight: RGBA
    segment_alpha: float
    segment_edge: RGBA
    text: RGBA
    hover_start: RGBA
    hover_end: RGBA
    hover_text: RGBA
    hub_fill: RGBA
    hub_border: RGBA
    pill_fill: RGBA
    pill_text: RGBA
    progress: RGBA


# The default look: milky glass, cream-peach petals, orange-red hover.
WARM = Palette(
    disc_top=(0.95, 0.80, 0.64, 0.40),
    disc_bottom=(0.90, 0.70, 0.55, 0.36),
    disc_border=(1.0, 0.98, 0.94, 0.85),
    shadow=(0.28, 0.12, 0.04, 0.20),
    segment_top=hex_rgba("#fcdcbf"),
    segment_bottom=hex_rgba("#f6c6a2"),
    segment_highlight=(1.0, 1.0, 1.0, 0.30),
    segment_alpha=0.95,
    segment_edge=(0.55, 0.30, 0.12, 0.22),
    text=hex_rgba("#3a2a22"),
    hover_start=hex_rgba("#e8462a"),
    hover_end=hex_rgba("#f05a28"),
    hover_text=(1.0, 1.0, 1.0, 1.0),
    hub_fill=(1.0, 0.96, 0.90, 0.08),
    hub_border=(1.0, 0.95, 0.88, 0.70),
    pill_fill=(1.0, 0.97, 0.94, 0.82),
    pill_text=hex_rgba("#3a2a22"),
    progress=hex_rgba("#e8462a"),
)

# Dark-mode variant (opt-in via RINGCONVERT_THEME=system): same warmth, smoked glass.
DARK = Palette(
    disc_top=(0.20, 0.15, 0.13, 0.62),
    disc_bottom=(0.14, 0.10, 0.09, 0.56),
    disc_border=(1.0, 0.85, 0.75, 0.28),
    shadow=(0.0, 0.0, 0.0, 0.40),
    segment_top=hex_rgba("#5a4135"),
    segment_bottom=hex_rgba("#4a342a"),
    segment_highlight=(1.0, 0.85, 0.75, 0.22),
    segment_alpha=0.92,
    segment_edge=(0.0, 0.0, 0.0, 0.30),
    text=hex_rgba("#f6d2b8"),
    hover_start=hex_rgba("#e8462a"),
    hover_end=hex_rgba("#f05a28"),
    hover_text=(1.0, 1.0, 1.0, 1.0),
    hub_fill=(1.0, 0.85, 0.75, 0.06),
    hub_border=(1.0, 0.85, 0.75, 0.30),
    pill_fill=(0.30, 0.22, 0.19, 0.85),
    pill_text=hex_rgba("#f6d2b8"),
    progress=hex_rgba("#f05a28"),
)

FONT_FAMILY = "Adwaita Sans, Cantarell, Inter, Noto Sans, sans-serif"


@dataclass
class Geometry:
    """All sizes derive from the glass disc radius."""

    radius: float = 200.0
    margin: float = 30.0  # room for the drop shadow and the hover scale-up

    @property
    def size(self) -> int:
        return int(math.ceil(2 * (self.radius + self.margin)))

    @property
    def center(self) -> tuple[float, float]:
        return self.size / 2, self.size / 2

    @property
    def outer(self) -> float:
        return self.radius * 0.94

    @property
    def inner(self) -> float:
        return self.radius * 0.39

    @property
    def hub(self) -> float:
        return self.radius * 0.355

    @property
    def gap(self) -> float:
        return self.radius * 0.03

    @property
    def corner(self) -> float:
        """Rounding of the petal's outer corners (the inner ones are tighter)."""
        return self.radius * 0.115

    @property
    def inner_corner(self) -> float:
        return self.radius * 0.06

    @property
    def label_radius(self) -> float:
        return (self.inner + self.outer) / 2 + self.radius * 0.01

    def span(self, count: int) -> float:
        return 2 * math.pi / count

    def mid_angle(self, count: int, index: int) -> float:
        """Segment 0 is centred at the top; indices go clockwise."""
        return -math.pi / 2 + index * self.span(count)

    def hit(self, x: float, y: float, count: int) -> int | None:
        """Segment under (x, y), or None for the hub / outside the disc.

        The gaps between petals belong to the nearest petal so the
        highlight doesn't flicker while moving across them.
        """
        if count <= 0:
            return None
        cx, cy = self.center
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        if r < self.hub or r > self.radius:
            return None
        span = self.span(count)
        rel = (math.atan2(dy, dx) + math.pi / 2 + span / 2) % (2 * math.pi)
        return min(int(rel // span), count - 1)

    def in_disc(self, x: float, y: float) -> bool:
        cx, cy = self.center
        return math.hypot(x - cx, y - cy) <= self.radius


@dataclass
class Segment:
    key: str
    label: str
    enabled: bool = True
    icon: str | None = None  # a key of ICONS, drawn above the label (tools ring)


@dataclass
class RingState:
    """Everything the painter needs for one frame."""

    segments: list[Segment] = field(default_factory=list)
    hover: list[float] = field(default_factory=list)  # 0..1 per segment
    readout: str = ""
    scale: float = 1.0
    alpha: float = 1.0
    labels_alpha: float = 1.0
    pulse_index: int | None = None
    pulse: float = 0.0  # 0..1..0 bump
    progress: float | None = None  # 0..1, or -1 for indeterminate
    spin: float = 0.0  # radians, for indeterminate progress
    placeholder_count: int = 8  # empty petals shown before the file type is known


class RingPainter:
    def __init__(self, geometry: Geometry | None = None, palette: Palette = WARM):
        self.geo = geometry or Geometry()
        self.palette = palette

    # ------------------------------------------------------------ shapes

    def _segment_path(self, cr: cairo.Context, count: int, index: int) -> None:
        """Annular sector between straight, even gaps, with each corner
        replaced by a fillet arc tangent to both of its edges: big round
        corners at the rim, tighter ones next to the hub."""
        g = self.geo
        cx, cy = g.center
        mid, half = g.mid_angle(count, index), g.span(count) / 2
        h = g.gap / 2

        def corner(radius: float, fillet: float, outer: bool, side: int):
            """Fillet between the arc of `radius` and the gap edge on `side`
            (-1: start, +1: end). Returns (centre, arc tangent angle about the
            ring centre, line tangent angle about the fillet centre, arc
            tangent angle about the fillet centre)."""
            dist = radius - fillet if outer else radius + fillet
            off = h + fillet
            along = math.sqrt(max(dist * dist - off * off, 0.0))
            edge = mid + side * half
            # Angle of the fillet centre as seen from the ring centre.
            theta = edge - side * math.atan2(off, along)
            fx, fy = cx + dist * math.cos(theta), cy + dist * math.sin(theta)
            # Towards the gap edge: perpendicular to the edge's radial line.
            to_line = edge + side * math.pi / 2
            to_arc = theta if outer else theta + math.pi
            return (fx, fy), theta, to_line, to_arc

        ro, ri = g.outer, g.inner
        fo = min(g.corner, (ro - ri) / 3)
        fi = min(g.inner_corner, (ro - ri) / 4)
        (c0, t0, l0, a0) = corner(ro, fo, True, -1)
        (c1, t1, l1, a1) = corner(ro, fo, True, +1)
        (c2, t2, l2, a2) = corner(ri, fi, False, +1)
        (c3, t3, l3, a3) = corner(ri, fi, False, -1)
        if t3 > t2:  # very many segments: the inner edge collapses
            t2 = t3 = mid
        cr.new_path()
        cr.arc(*c0, fo, l0, a0)
        cr.arc(cx, cy, ro, t0, t1)
        cr.arc(*c1, fo, a1, l1)
        cr.arc(*c2, fi, l2, a2)
        cr.arc_negative(cx, cy, ri, t2, t3)
        cr.arc(*c3, fi, a3, l3)
        cr.close_path()

    def _fill_segment(self, cr, count, index, top: RGBA, bottom: RGBA, highlight: RGBA | None) -> None:
        g = self.geo
        cx, cy = g.center
        mid = g.mid_angle(count, index)
        # Gradient runs from the outer rim (lighter) towards the hub.
        ox, oy = cx + g.outer * math.cos(mid), cy + g.outer * math.sin(mid)
        ix, iy = cx + g.inner * math.cos(mid), cy + g.inner * math.sin(mid)
        grad = cairo.LinearGradient(ox, oy, ix, iy)
        grad.add_color_stop_rgba(0, *top)
        grad.add_color_stop_rgba(1, *bottom)
        self._segment_path(cr, count, index)
        cr.set_source(grad)
        cr.fill_preserve()
        # Glass edge: a faint dark hairline outside, a bright one inside.
        cr.set_line_width(max(1.0, g.radius * 0.007))
        cr.set_source_rgba(*self.palette.segment_edge)
        cr.stroke_preserve()
        cr.save()
        cr.clip_preserve()
        cr.set_line_width(max(2.0, g.radius * 0.016))
        edge = cairo.LinearGradient(cx, cy - g.outer, cx, cy + g.outer)
        edge.add_color_stop_rgba(0, 1, 1, 1, 0.55)
        edge.add_color_stop_rgba(1, 1, 1, 1, 0.25)
        cr.set_source(edge)
        cr.stroke()
        cr.restore()
        if highlight:
            # Soft inner sheen along the outer edge, only on the petal's pixels.
            sheen = cairo.RadialGradient(cx, cy, g.outer - g.radius * 0.16, cx, cy, g.outer)
            sheen.add_color_stop_rgba(0, *highlight[:3], 0)
            sheen.add_color_stop_rgba(1, *highlight)
            cr.set_operator(cairo.OPERATOR_ATOP)
            cr.set_source(sheen)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

    # ------------------------------------------------------------ text

    def _layout(self, cr, text: str, px: float, spacing: float = 0.0):
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string(FONT_FAMILY)
        font.set_weight(Pango.Weight.BOLD)
        font.set_absolute_size(px * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_text(text, -1)
        if spacing:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
            layout.set_attributes(attrs)
        return layout

    def _text_centered(self, cr, text: str, x: float, y: float, px: float, color: RGBA,
                       spacing: float = 0.0) -> None:
        layout = self._layout(cr, text, px, spacing)
        _ink, logical = layout.get_pixel_extents()
        cr.set_source_rgba(*color)
        cr.move_to(x - logical.width / 2 - logical.x, y - logical.height / 2 - logical.y)
        PangoCairo.show_layout(cr, layout)
        cr.new_path()  # drop the current point so later arcs don't connect to it

    # ------------------------------------------------------------ frame

    def paint(self, cr: cairo.Context, state: RingState) -> None:
        if state.alpha <= 0.001:
            return
        g, p = self.geo, self.palette
        cx, cy = g.center
        cr.save()
        cr.translate(cx, cy)
        cr.scale(state.scale, state.scale)
        cr.translate(-cx, -cy)
        cr.push_group()

        self._paint_shadow(cr)
        self._paint_disc(cr)

        count = len(state.segments) or state.placeholder_count
        # Draw hovered petals last so their scale-up overlaps neighbours.
        order = sorted(range(count), key=lambda i: state.hover[i] if i < len(state.hover) else 0)
        for i in order:
            self._paint_segment(cr, state, count, i)

        self._paint_hub(cr, state)
        cr.pop_group_to_source()
        cr.paint_with_alpha(state.alpha)
        cr.restore()

    def _paint_shadow(self, cr) -> None:
        g, p = self.geo, self.palette
        cx, cy = g.center
        # Shadow only outside the disc, so the glass doesn't look dirty.
        cr.save()
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.arc(cx, cy, g.radius + g.margin, 0, 2 * math.pi)
        cr.new_sub_path()
        cr.arc(cx, cy, g.radius - 0.5, 0, 2 * math.pi)
        cr.clip()
        reach = min(g.margin, g.radius * 0.11)
        oy = cy + g.radius * 0.025
        shade = cairo.RadialGradient(cx, oy, g.radius * 0.96, cx, oy, g.radius + reach)
        shade.add_color_stop_rgba(0, *p.shadow)
        shade.add_color_stop_rgba(0.3, *p.shadow[:3], p.shadow[3] * 0.5)
        shade.add_color_stop_rgba(1, *p.shadow[:3], 0)
        cr.set_source(shade)
        cr.paint()
        cr.restore()

    def _paint_disc(self, cr) -> None:
        g, p = self.geo, self.palette
        cx, cy = g.center
        glass = cairo.LinearGradient(cx, cy - g.radius, cx, cy + g.radius)
        glass.add_color_stop_rgba(0, *p.disc_top)
        glass.add_color_stop_rgba(1, *p.disc_bottom)
        cr.arc(cx, cy, g.radius, 0, 2 * math.pi)
        cr.set_source(glass)
        cr.fill()
        # Bright rim, a little stronger at the top like light on glass.
        rim = cairo.LinearGradient(cx, cy - g.radius, cx, cy + g.radius)
        rim.add_color_stop_rgba(0, *p.disc_border)
        rim.add_color_stop_rgba(1, *p.disc_border[:3], p.disc_border[3] * 0.55)
        w = max(1.5, g.radius * 0.008)
        cr.arc(cx, cy, g.radius - w / 2, 0, 2 * math.pi)
        cr.set_line_width(w)
        cr.set_source(rim)
        cr.stroke()
        cr.arc(cx, cy, g.radius - w * 2, 0, 2 * math.pi)
        cr.set_line_width(w)
        cr.set_source_rgba(*p.disc_border[:3], p.disc_border[3] * 0.25)
        cr.stroke()

    def _paint_segment(self, cr, state: RingState, count: int, i: int) -> None:
        g, p = self.geo, self.palette
        cx, cy = g.center
        seg = state.segments[i] if i < len(state.segments) else None
        h = state.hover[i] if i < len(state.hover) else 0.0
        if seg is not None and not seg.enabled:
            h = 0.0
        pulse = state.pulse if state.pulse_index == i else 0.0
        scale = 1 + 0.045 * h + 0.07 * pulse

        mid = g.mid_angle(count, i)
        lx, ly = cx + g.label_radius * math.cos(mid), cy + g.label_radius * math.sin(mid)
        cr.save()
        cr.translate(lx, ly)
        cr.scale(scale, scale)
        cr.translate(-lx, -ly)

        cr.push_group()
        self._fill_segment(cr, count, i, p.segment_top, p.segment_bottom, p.segment_highlight)
        cr.pop_group_to_source()
        alpha = p.segment_alpha * (0.45 if seg is not None and not seg.enabled else 1.0)
        cr.paint_with_alpha(alpha * (1 - h))

        if h > 0:
            cr.push_group()
            self._fill_segment(cr, count, i, p.hover_start, p.hover_end, (1, 1, 1, 0.18 + 0.3 * pulse))
            cr.pop_group_to_source()
            cr.paint_with_alpha(h)

        if seg is not None and seg.label:
            color = mix(p.text, p.hover_text, h)
            color = color[:3] + (color[3] * state.labels_alpha * (0.5 if not seg.enabled else 1),)
            if seg.icon in ICONS:
                # Tools ring: a small line icon above a smaller label.
                icon = g.radius * 0.12
                draw_icon(cr, seg.icon, lx, ly - g.radius * 0.058, icon, color, g.radius * 0.012)
                self._text_centered(cr, seg.label, lx, ly + g.radius * 0.06, g.radius * 0.062, color,
                                    spacing=g.radius * 0.005)
            else:
                size = g.radius * (0.096 if len(seg.label) <= 4 else 0.078)
                self._text_centered(cr, seg.label, lx, ly, size, color, spacing=g.radius * 0.004)
        cr.restore()

    def _paint_hub(self, cr, state: RingState) -> None:
        g, p = self.geo, self.palette
        cx, cy = g.center
        cr.new_path()
        cr.arc(cx, cy, g.hub, 0, 2 * math.pi)
        cr.set_source_rgba(*p.hub_fill)
        cr.fill()
        # A thick, soft glass rim around the hub, brighter at the top.
        rim_w = g.radius * 0.048
        rim = cairo.LinearGradient(cx, cy - g.hub, cx, cy + g.hub)
        rim.add_color_stop_rgba(0, *p.hub_border)
        rim.add_color_stop_rgba(1, *p.hub_border[:3], p.hub_border[3] * 0.5)
        cr.arc(cx, cy, g.hub - rim_w / 2, 0, 2 * math.pi)
        cr.set_line_width(rim_w)
        cr.set_source(rim)
        cr.stroke()

        if state.progress is not None:
            r = g.hub - g.radius * 0.035
            cr.set_line_width(g.radius * 0.022)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(*p.progress[:3], 0.18)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgba(*p.progress)
            if state.progress < 0:
                start = state.spin
                cr.arc(cx, cy, r, start, start + math.pi * 0.6)
            else:
                start = -math.pi / 2
                cr.arc(cx, cy, r, start, start + 2 * math.pi * max(0.02, min(1.0, state.progress)))
            cr.stroke()

        if not state.readout:
            return
        # The pill's text matches the ring's labels (smaller on the tools ring).
        px = g.radius * (0.07 if any(s.icon for s in state.segments) else 0.088)
        layout = self._layout(cr, state.readout, px, spacing=g.radius * 0.004)
        _ink, logical = layout.get_pixel_extents()
        height = g.radius * 0.24
        width = min(max(logical.width + height * 1.1, g.hub * 1.5), g.hub * 1.85)
        x, y = cx - width / 2, cy - height / 2

        def pill(dy: float = 0.0, grow: float = 0.0) -> None:
            r = height / 2 + grow
            cr.new_sub_path()
            cr.arc(x + width - height / 2, y + height / 2 + dy, r, -math.pi / 2, math.pi / 2)
            cr.arc(x + height / 2, y + height / 2 + dy, r, math.pi / 2, 3 * math.pi / 2)
            cr.close_path()

        # Soft shadow under the pill, then the frosted pill with a bright edge.
        for grow, alpha in ((g.radius * 0.02, 0.05), (g.radius * 0.008, 0.08)):
            pill(g.radius * 0.015, grow)
            cr.set_source_rgba(*p.shadow[:3], alpha)
            cr.fill()
        pill()
        sheen = cairo.LinearGradient(0, y, 0, y + height)
        sheen.add_color_stop_rgba(0, *p.pill_fill[:3], min(1.0, p.pill_fill[3] + 0.08))
        sheen.add_color_stop_rgba(1, *p.pill_fill[:3], p.pill_fill[3] - 0.08)
        cr.set_source(sheen)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.7 * p.pill_fill[3])
        cr.set_line_width(max(1.0, g.radius * 0.006))
        cr.stroke()
        layout.set_width(int((width - height * 0.5) * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(Pango.Alignment.CENTER)
        _ink, logical = layout.get_pixel_extents()
        cr.set_source_rgba(*p.pill_text)
        cr.move_to(cx - (width - height * 0.5) / 2, cy - logical.height / 2 - logical.y)
        PangoCairo.show_layout(cr, layout)


# ---------------------------------------------------------------- icons
# Line icons for the tools ring, drawn on a 24-unit grid.


def _icon_compress(cr) -> None:
    for (x0, y0), (x1, y1), head in (((4, 4), (10, 10), ((10, 5), (5, 10))),
                                     ((20, 20), (14, 14), ((14, 19), (19, 14)))):
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.move_to(*head[0])
        cr.line_to(x1, y1)
        cr.line_to(*head[1])
    cr.stroke()


def _icon_metadata(cr) -> None:
    # A luggage tag: square corner top right, point bottom left, with a hole.
    cr.move_to(13, 3)
    cr.line_to(19, 3)
    cr.arc(19, 5, 2, -math.pi / 2, 0)
    cr.line_to(21, 11)
    cr.line_to(12.5, 19.5)
    cr.arc(11, 18, 2.1, math.pi / 4, 3 * math.pi / 4)
    cr.line_to(4.5, 12.5)
    cr.arc(6, 11, 2.1, 3 * math.pi / 4, 5 * math.pi / 4)
    cr.close_path()
    cr.stroke()
    cr.arc(16.5, 7.5, 1.4, 0, 2 * math.pi)
    cr.stroke()


def _icon_edit(cr) -> None:
    # Three sliders, each line broken around its knob.
    for y, knob in ((6, 15), (12, 8), (18, 13)):
        cr.move_to(3, y)
        cr.line_to(knob - 2.6, y)
        cr.move_to(knob + 2.6, y)
        cr.line_to(21, y)
        cr.stroke()
        cr.arc(knob, y, 2.4, 0, 2 * math.pi)
        cr.stroke()


def _icon_annotate(cr) -> None:
    # A circle open at the top right, with a pencil crossing it.
    cr.arc(11, 13, 8.5, math.radians(-25), math.radians(285))
    cr.stroke()
    cr.move_to(10, 14)
    cr.line_to(19.5, 4.5)
    cr.stroke()
    cr.move_to(10, 14)
    cr.line_to(9, 15.5)
    cr.stroke()


def _icon_add_bg(cr) -> None:
    # A framed picture: mountains and a sun.
    x, y, w, h, r = 3, 5, 18, 14, 2.5
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.stroke()
    cr.move_to(5.5, 16.5)
    cr.line_to(10, 11.5)
    cr.line_to(13.5, 15)
    cr.line_to(15.5, 13)
    cr.line_to(18.5, 16.5)
    cr.stroke()
    cr.arc(15.5, 9, 1.5, 0, 2 * math.pi)
    cr.fill()


def _icon_crop(cr) -> None:
    cr.move_to(7, 2)
    cr.line_to(7, 17)
    cr.line_to(22, 17)
    cr.move_to(2, 7)
    cr.line_to(17, 7)
    cr.line_to(17, 22)
    cr.stroke()


def _icon_redact(cr) -> None:
    # An eye with a slash through it.
    cr.move_to(2.5, 12)
    cr.curve_to(6, 5.5, 18, 5.5, 21.5, 12)
    cr.curve_to(18, 18.5, 6, 18.5, 2.5, 12)
    cr.close_path()
    cr.stroke()
    cr.arc(12, 12, 3, 0, 2 * math.pi)
    cr.stroke()
    cr.move_to(4, 3.5)
    cr.line_to(20, 20.5)
    cr.stroke()


ICONS = {
    "compress": _icon_compress,
    "metadata": _icon_metadata,
    "edit": _icon_edit,
    "annotate": _icon_annotate,
    "add_bg": _icon_add_bg,
    "crop": _icon_crop,
    "redact": _icon_redact,
}


def draw_icon(cr, name: str, x: float, y: float, size: float, color: RGBA, width: float) -> None:
    """Draw icon `name` centred on (x, y), `size` px square, with `width` px lines."""
    k = size / 24
    cr.save()
    cr.translate(x - size / 2, y - size / 2)
    cr.scale(k, k)
    cr.set_line_width(width / k)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(*color)
    cr.new_path()
    ICONS[name](cr)
    cr.restore()
    cr.new_path()


def render_png(path: str, state: RingState, painter: RingPainter | None = None,
               background=None, scale: float = 1.0) -> None:
    """Render one frame to PNG. `background(cr, w, h)` may paint a backdrop."""
    painter = painter or RingPainter()
    size = int(painter.geo.size * scale)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    if background:
        background(cr, size, size)
    cr.scale(scale, scale)
    painter.paint(cr, state)
    surface.write_to_png(path)


# ---------------------------------------------------------------- animation


class Tween:
    """A value easing towards a target over a fixed duration."""

    def __init__(self, value: float = 0.0):
        self.start_value = self.target = self.value = value
        self.start = 0.0
        self.duration = 0.0

    def to(self, target: float, duration: float, now: float) -> None:
        if target == self.target and self.active(now):
            return
        self.start_value, self.target = self.value, target
        self.start, self.duration = now, duration

    def set(self, value: float) -> None:
        self.start_value = self.target = self.value = value
        self.duration = 0.0

    def active(self, now: float) -> bool:
        return self.duration > 0 and now < self.start + self.duration

    def update(self, now: float) -> float:
        if self.duration <= 0 or now >= self.start + self.duration:
            self.value = self.target
        else:
            t = ease_out((now - self.start) / self.duration)
            self.value = self.start_value + (self.target - self.start_value) * t
        return self.value


APPEAR = 0.18
HOVER = 0.12
PULSE = 0.26
DISMISS = 0.16


class RingWidget(Gtk.DrawingArea):
    """Draws and animates the ring. The window feeds it segments and pointer
    positions; it reports nothing itself apart from hit-testing."""

    def __init__(self, painter: RingPainter | None = None):
        super().__init__()
        self.painter = painter or RingPainter()
        size = self.painter.geo.size
        self.set_content_width(size)
        self.set_content_height(size)
        self.state = RingState()
        self._open = Tween(0.0)
        self._hover: list[Tween] = []
        self._labels = Tween(1.0)
        self._pulse = Tween(0.0)
        self._pulse_up = False
        self._closing = False
        self._after_dismiss = None
        self._after_pulse = None
        self._tick_id = 0
        self.hovered: int | None = None
        self.set_draw_func(self._draw)

    @property
    def geo(self) -> Geometry:
        return self.painter.geo

    # ------------------------------------------------------------ API

    def set_segments(self, segments: list[Segment]) -> None:
        changed = [(s.key, s.enabled) for s in segments] != [(s.key, s.enabled) for s in self.state.segments]
        self.state.segments = segments
        if changed:
            self._hover = [Tween(0.0) for _ in segments]
            self.hovered = None
            self._labels.set(0.0)
            self._labels.to(1.0, HOVER, self._now())
            self._kick()

    def set_readout(self, text: str) -> None:
        if text != self.state.readout:
            self.state.readout = text
            self.queue_draw()

    def set_progress(self, progress: float | None) -> None:
        self.state.progress = progress
        self._kick() if progress is not None and progress < 0 else self.queue_draw()

    def hit(self, x: float, y: float) -> int | None:
        return self.geo.hit(x, y, len(self.state.segments))

    def set_hovered(self, index: int | None) -> None:
        if index is not None and not self.state.segments[index].enabled:
            index = None
        if index == self.hovered:
            return
        self.hovered = index
        now = self._now()
        for i, tween in enumerate(self._hover):
            tween.to(1.0 if i == index else 0.0, HOVER, now)
        self._kick()

    def appear(self) -> None:
        self._closing = False
        self._after_dismiss = None
        now = self._now()
        self._open.set(0.0)
        self._open.to(1.0, APPEAR, now)
        self._kick()

    def pulse(self, index: int, then=None) -> None:
        self.state.pulse_index = index
        self._pulse.set(0.0)
        self._pulse.to(1.0, PULSE / 2, self._now())
        self._pulse_up = True
        self._after_pulse = then
        self._kick()

    @property
    def closing(self) -> bool:
        return self._closing

    def dismiss(self, then=None) -> None:
        self._closing = True
        self._after_dismiss = then
        self._open.to(0.0, DISMISS, self._now())
        self._kick()

    # ------------------------------------------------------------ frames

    def _now(self) -> float:
        clock = self.get_frame_clock()
        return clock.get_frame_time() / 1e6 if clock else time.monotonic()

    def _kick(self) -> None:
        if not self._tick_id:
            self._tick_id = self.add_tick_callback(self._tick)
        self.queue_draw()

    def _tick(self, _widget, clock) -> bool:
        now = clock.get_frame_time() / 1e6
        busy = False
        for tween in (self._open, self._labels, self._pulse, *self._hover):
            tween.update(now)
            busy |= tween.active(now)
        if self._pulse_up and not self._pulse.active(now):
            self._pulse_up = False
            self._pulse.to(0.0, PULSE / 2, now)
            busy = True
        elif self._after_pulse and not self._pulse.active(now):
            callback, self._after_pulse = self._after_pulse, None
            GLib.idle_add(callback)
        if self.state.progress is not None and self.state.progress < 0:
            self.state.spin = (now * 2 * math.pi * 0.9) % (2 * math.pi)
            busy = True
        if self._closing and not self._open.active(now) and self._after_dismiss:
            callback, self._after_dismiss = self._after_dismiss, None
            GLib.idle_add(callback)
        self.queue_draw()
        if not busy and not self._after_pulse and not self._after_dismiss:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr, _width, _height) -> None:
        s = self.state
        open_ = self._open.value
        if self._closing:
            s.scale = 0.94 + 0.06 * open_
        else:
            s.scale = 0.85 + 0.15 * open_
        s.alpha = open_
        s.labels_alpha = self._labels.value
        s.hover = [t.value for t in self._hover]
        s.pulse = self._pulse.value
        self.painter.paint(cr, s)
