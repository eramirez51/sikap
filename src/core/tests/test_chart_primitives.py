from array import array

import pytest

from core.chart.primitives import Overlay, Polyline, Rect


def test_polyline_is_frozen():
    p = Polyline(xs=array("d", [0, 1]), ys=array("d", [0, 1]),
                 color=(1, 0, 0, 1), width=1.0)
    with pytest.raises(Exception):
        p.width = 2.0  # type: ignore[misc]


def test_polyline_default_width():
    p = Polyline(xs=array("d"), ys=array("d"), color=(1, 1, 1, 1))
    assert p.width == 1.0


def test_overlay_default_empty_polylines():
    o = Overlay(name="x")
    assert o.polylines == ()


def test_overlay_holds_polylines():
    p1 = Polyline(xs=array("d", [0]), ys=array("d", [1]), color=(1, 0, 0, 1))
    p2 = Polyline(xs=array("d", [0]), ys=array("d", [2]), color=(0, 1, 0, 1))
    o = Overlay(name="vwap", polylines=(p1, p2))
    assert o.polylines == (p1, p2)


def test_rect_is_frozen():
    r = Rect(x1=0, y1=100, x2=10, y2=110, color=(0, 0, 1, 0.2))
    with pytest.raises(Exception):
        r.x1 = 5  # type: ignore[misc]


def test_overlay_default_empty_rects():
    o = Overlay(name="x")
    assert o.rects == ()


def test_overlay_holds_rects():
    r1 = Rect(x1=0, y1=100, x2=10, y2=110, color=(0, 0, 1, 0.2))
    r2 = Rect(x1=0, y1=120, x2=10, y2=130, color=(1, 0, 0, 0.2))
    o = Overlay(name="hvn", rects=(r1, r2))
    assert o.rects == (r1, r2)
