# SPDX-License-Identifier: GPL-3.0-or-later
import math

import pytest

cairo = pytest.importorskip("cairo")
ring_widget = pytest.importorskip("ringconvert.ring_widget")
Geometry, RingState, Segment = ring_widget.Geometry, ring_widget.RingState, ring_widget.Segment


def point(geo, angle_deg, radius):
    cx, cy = geo.center
    a = math.radians(angle_deg)
    return cx + radius * math.cos(a), cy + radius * math.sin(a)


@pytest.mark.parametrize("count", [4, 5, 7, 9])
def test_segments_clockwise_from_top(count):
    geo = Geometry()
    r = geo.label_radius
    assert geo.hit(*point(geo, -90, r), count) == 0
    # One step clockwise (screen y grows downwards, so +angle is clockwise).
    assert geo.hit(*point(geo, -90 + 360 / count, r), count) == 1
    assert geo.hit(*point(geo, -90 - 360 / count, r), count) == count - 1


def test_hub_and_outside_are_not_segments():
    geo = Geometry()
    assert geo.hit(*geo.center, 9) is None
    assert geo.hit(*point(geo, 0, geo.hub * 0.9), 9) is None
    assert geo.hit(*point(geo, 0, geo.radius + 5), 9) is None
    assert geo.hit(0, 0, 9) is None


def test_gaps_belong_to_a_segment():
    geo = Geometry()
    # Exactly on the boundary between segment 0 and 1 of an 8-ring.
    assert geo.hit(*point(geo, -90 + 22.5, geo.label_radius), 8) in (0, 1)


def _render_pixels(tmp_path, palette):
    segs = [Segment(k, k.upper()) for k in ("png", "webp", "heic", "gif")]
    state = RingState(segments=segs, hover=[0, 1, 0, 0], readout="WEBP")
    out = tmp_path / "ring.png"
    ring_widget.render_png(str(out), state, ring_widget.RingPainter(palette=palette))
    surface = cairo.ImageSurface.create_from_png(str(out))
    data, stride = surface.get_data(), surface.get_stride()

    def pixel(x, y):  # premultiplied BGRA
        i = int(y) * stride + int(x) * 4
        b, g, r, a = data[i:i + 4]
        return r, g, b, a

    geo = Geometry()
    # Inside the hovered (right-hand) petal and a resting one, away from their labels.
    hovered = pixel(*point(geo, 0, geo.outer - geo.radius * 0.08))
    resting = pixel(*point(geo, 180, geo.outer - geo.radius * 0.08))
    return hovered, resting, pixel(1, 1)


def test_render_smoke_is_black_with_bright_hover(tmp_path):
    hovered, resting, corner = _render_pixels(tmp_path, ring_widget.SMOKE)
    r, g, b, a = hovered
    assert min(r, g, b) > 200 and max(r, g, b) - min(r, g, b) < 12 and a > 240
    r, g, b, a = resting
    assert max(r, g, b) < 70 and max(r, g, b) - min(r, g, b) < 12 and a > 230
    # Corners of the window stay fully transparent.
    assert corner[3] == 0


def test_render_warm_hover_is_orange(tmp_path):
    hovered, resting, corner = _render_pixels(tmp_path, ring_widget.WARM)
    r, g, b, a = hovered
    assert r > 200 and g < 120 and b < 80 and a > 240
    # A resting petal is light peach.
    r, g, b, a = resting
    assert r > 180 and g > 150 and b > 120
    assert corner[3] == 0


def test_default_palette_is_smoke(monkeypatch):
    ring_window = pytest.importorskip("ringconvert.ring_window")
    monkeypatch.delenv("RINGCONVERT_THEME", raising=False)
    assert ring_window._palette() is ring_widget.SMOKE
    assert ring_widget.RingPainter().palette is ring_widget.SMOKE
    monkeypatch.setenv("RINGCONVERT_THEME", "warm")
    assert ring_window._palette() is ring_widget.WARM


def test_tool_icons_render(tmp_path):
    keys = ["compress", "metadata", "edit", "annotate", "add_bg", "crop", "redact"]
    assert set(keys) <= set(ring_widget.ICONS)
    segs = [Segment(k, k.upper(), icon=k) for k in keys]
    state = RingState(segments=segs, hover=[0, 0, 0, 0, 0, 1, 0], readout="CROP")
    ring_widget.render_png(str(tmp_path / "tools.png"), state)


def test_tool_segments_enable_working_tools():
    ring_window = pytest.importorskip("ringconvert.ring_window")
    tools = ["compress", "add_bg", "crop"]
    single = {s.key: s.enabled for s in ring_window.tool_segments(tools, 1)}
    assert single == {"compress": True, "add_bg": True, "crop": True}
    batch = {s.key: s.enabled for s in ring_window.tool_segments(tools, 3)}
    assert batch == {"compress": True, "add_bg": True, "crop": False}
    assert all(s.icon == s.key for s in ring_window.tool_segments(tools, 0))


def _app_or_skip():
    gtk = pytest.importorskip("gi.repository.Gtk")
    if not gtk.init_check():
        pytest.skip("no display")
    app_mod = pytest.importorskip("ringconvert.app")
    return app_mod.RingConvertApp()


def test_tools_window_builds_its_menu_before_any_file():
    ring_window = pytest.importorskip("ringconvert.ring_window")
    win = ring_window.RingWindow(_app_or_skip(), mode="tools")
    assert win.items == ring_window.ALL_TOOLS
    assert [s.key for s in win.ring.state.segments] == ring_window.ALL_TOOLS
    assert win.ring.state.readout == "Drop a file"


def test_window_title_names_the_mode():
    # The Shell extension finds the ring and reads its mode from the title.
    ring_window = pytest.importorskip("ringconvert.ring_window")
    win = ring_window.RingWindow(_app_or_skip())
    assert win.get_title() == "RingConvert"
    win.toggle_mode()
    assert win.get_title() == "RingConvert Tools"
    win.toggle_mode()
    assert win.get_title() == "RingConvert"
