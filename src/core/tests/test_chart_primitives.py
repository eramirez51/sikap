from array import array

import pytest

from core.chart.primitives import Overlay, Polyline


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
