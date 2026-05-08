from core.candle import Candle
from core.chart.time_axis import TimeAxis


def _c(ts):
    return Candle(ts=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=0)


def _axis(bar_width, base_index, width):
    return TimeAxis(bar_width=bar_width, base_index=base_index, width=width)


def test_x_for_bar_places_rightmost_at_right_edge():
    axis = _axis(10.0, 9, 100.0)
    assert axis.x_for_bar(9) == 95.0
    assert axis.x_for_bar(8) == 85.0


def test_projection_matches_x_for_bar():
    axis = _axis(8.0, 50, 800.0)
    p = axis.projection()
    for i in (0, 1, 25, 49, 50):
        from_proj = i * p.bar_spacing + p.x_offset
        assert abs(axis.x_for_bar(i) - from_proj) < 1e-3


def test_visible_range_inclusive():
    axis = _axis(10.0, 9, 100.0)
    first, last = axis.visible_range()
    assert last == 9
    assert first <= last


def test_labels_emit_at_calendar_boundaries():
    candles = [_c(i * 3600) for i in range(24)]   # 24 H1 bars over 24h
    axis = _axis(20.0, 23, 24.0 * 20.0)
    labels = axis.labels(candles, 400.0)
    assert 5 <= len(labels) <= 7
    assert labels[0].text == "Jan 1"


def test_labels_show_time_when_subhour():
    base_ts = 9 * 3600                            # 09:00 UTC
    candles = [_c(base_ts + i * 900) for i in range(12)]
    axis = _axis(40.0, 11, 12.0 * 40.0)
    labels = axis.labels(candles, 400.0)
    assert labels
    for lbl in labels:
        assert ":" in lbl.text


def test_labels_empty_for_degenerate():
    axis = _axis(10.0, 0, 100.0)
    assert axis.labels([], 400.0) == []
    assert axis.labels([_c(0)], 400.0) == []


def test_zoom_bar_width_multiplies_and_clamps():
    axis = _axis(8.0, 0, 800.0)
    axis.zoom_bar_width(2.0)
    assert axis.bar_width == 16.0
    axis.zoom_bar_width(100.0)           # would go to 1600, clamps to 64
    assert axis.bar_width == 64.0
    axis.zoom_bar_width(0.0001)          # would go below 2, clamps to 2
    assert axis.bar_width == 2.0


def test_zoom_bar_width_ignores_non_positive_factor():
    axis = _axis(8.0, 0, 800.0)
    axis.zoom_bar_width(0.0)
    axis.zoom_bar_width(-1.0)
    assert axis.bar_width == 8.0
