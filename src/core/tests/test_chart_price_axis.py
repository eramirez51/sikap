from core.candle import Candle
from core.chart.price_axis import PriceAxis


def _c(ts, o, h, l, cl):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=0)


def test_fit_to_padded_above_high_and_below_low():
    axis = PriceAxis.new(400.0)
    axis.fit_to([_c(0, 100.0, 110.0, 99.0, 104.0)])
    assert axis.price_min < 99.0
    assert axis.price_max > 110.0


def test_pan_shifts_range_in_screen_direction():
    axis = PriceAxis.new(100.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    axis.pan_by_px(10.0)            # 10% of height, span 100 → +10
    assert abs(axis.price_min - 110.0) < 1e-9
    assert abs(axis.price_max - 210.0) < 1e-9


def test_y_for_price_round_trip():
    axis = PriceAxis.new(400.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    for p in (100.0, 150.0, 200.0):
        y = axis.y_for_price(p)
        assert abs(axis.price_for_y(y) - p) < 1e-6


def test_labels_inset_to_right_of_inner_width():
    axis = PriceAxis.new(400.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    labels = axis.labels(800.0)
    assert labels
    for lbl in labels:
        assert lbl.x == 800.0 + 6.0      # LABEL_INSET_PX


def test_label_text_uses_decimals_heuristic():
    axis = PriceAxis.new(400.0)
    axis.price_min = 0.5
    axis.price_max = 1.5    # span 1.0 → 2 decimals
    labels = axis.labels(800.0)
    for lbl in labels:
        assert "." in lbl.text


def test_projection_consistent_with_y_for_price():
    axis = PriceAxis.new(400.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    proj = axis.projection()
    for p in (100.0, 150.0, 200.0):
        from_proj = p * proj.price_scale + proj.price_offset
        assert abs(axis.y_for_price(p) - from_proj) < 1e-3


def test_zoom_around_center_keeps_midpoint_and_scales_span():
    axis = PriceAxis.new(400.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    axis.zoom_around_center(2.0)         # widen 2× → span 200, center 150
    assert abs(axis.price_min - 50.0)  < 1e-9
    assert abs(axis.price_max - 250.0) < 1e-9

    axis.zoom_around_center(0.5)         # back to span 100, center 150
    assert abs(axis.price_min - 100.0) < 1e-9
    assert abs(axis.price_max - 200.0) < 1e-9


def test_zoom_around_center_ignores_non_positive_factor():
    axis = PriceAxis.new(400.0)
    axis.price_min = 100.0
    axis.price_max = 200.0
    axis.zoom_around_center(0.0)
    axis.zoom_around_center(-1.0)
    assert axis.price_min == 100.0
    assert axis.price_max == 200.0
