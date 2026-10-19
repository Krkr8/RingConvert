# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from ringconvert.dialogs import cropmath

W, H = 2550, 3300


def test_free_corner_drag_moves_one_corner():
    box = cropmath.drag(cropmath.full(W, H), "se", -550, -626, W, H)
    assert box == (0, 0, 2000, 2674)


def test_drag_is_clamped_to_the_image_and_min_size():
    assert cropmath.drag((0, 0, 100, 100), "nw", -50, -50, W, H) == (0, 0, 100, 100)
    x, y, w, h = cropmath.drag((0, 0, 100, 100), "se", -500, -500, W, H)
    assert (w, h) == (cropmath.MIN_SIZE, cropmath.MIN_SIZE)


def test_move_stays_inside():
    assert cropmath.drag((100, 100, 500, 500), "move", 10_000, -10_000, W, H) == (W - 500, 0, 500, 500)


def test_fit_ratio_is_largest_box_centred():
    x, y, w, h = cropmath.fit_ratio(cropmath.full(W, H), 1.0, W, H)
    assert (w, h) == (2550, 2550)
    assert y == pytest.approx((H - 2550) / 2)
    x, y, w, h = cropmath.fit_ratio(cropmath.full(W, H), 16 / 9, W, H)
    assert w == W and h == pytest.approx(W * 9 / 16)


@pytest.mark.parametrize("handle", ["nw", "ne", "sw", "se", "n", "s", "e", "w"])
def test_ratio_drag_keeps_ratio_and_bounds(handle):
    start = cropmath.fit_ratio(cropmath.full(W, H), 4 / 3, W, H)
    for dx, dy in ((-300, 120), (400, -50), (5000, 5000), (-5000, -5000)):
        x, y, w, h = cropmath.drag(start, handle, dx, dy, W, H, ratio=4 / 3)
        assert w / h == pytest.approx(4 / 3)
        assert x >= -1e-6 and y >= -1e-6
        assert x + w <= W + 1e-6 and y + h <= H + 1e-6


def test_corner_with_ratio_keeps_opposite_corner():
    start = (100, 100, 400, 300)
    x, y, w, h = cropmath.drag(start, "se", 200, 0, W, H, ratio=4 / 3)
    assert (x, y) == (100, 100) and (w, h) == (600, 450)
    x, y, w, h = cropmath.drag(start, "nw", -40, 0, W, H, ratio=4 / 3)
    assert (x + w, y + h) == (500, 400)


def test_resize_fields():
    assert cropmath.resize((0, 0, 100, 100), 800, None, W, H) == (0, 0, 800, 100)
    x, y, w, h = cropmath.resize((0, 0, 100, 100), 800, None, W, H, ratio=16 / 9)
    assert (w, h) == (800, 450)
    # Too big for the image: clamped, moved back inside.
    assert cropmath.resize((2000, 3000, 100, 100), 9999, 9999, W, H) == (0, 0, W, H)


def test_pixels_rounds_edges():
    assert cropmath.pixels((10.4, 10.6, 99.8, 50.2)) == (10, 11, 100, 50)
