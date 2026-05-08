from core.candle import Candle, Timeframe
from core.engine.pane import EnginePane


def _c(ts, o, h, l, cl):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=0)


def test_rebuild_snaps_base_index_and_fits_price():
    pane = EnginePane.new(0, "NQ", Timeframe.M15, 800.0, 400.0)
    candles = [
        _c(0, 100.0, 105.0,  99.0, 104.0),
        _c(1, 104.0, 106.0, 102.0, 103.0),
        _c(2, 103.0, 110.0, 103.0, 109.0),
    ]
    pane.rebuild(candles)
    assert len(pane.candles) == 3
    assert pane.time.base_index == 2
    assert pane.price.price_min < 99.0
    assert pane.price.price_max > 110.0
    assert pane.price.labels(800.0)


def test_zoom_by_clamps():
    pane = EnginePane.new(0, "NQ", Timeframe.M15, 800.0, 400.0)
    assert pane.zoom_by(-100.0)
    assert pane.time.bar_width == 2.0
    assert not pane.zoom_by(-1.0)            # already clamped
    assert pane.zoom_by(10.0)
    assert pane.time.bar_width == 12.0
    assert pane.zoom_by(1000.0)
    assert pane.time.bar_width == 64.0
    assert not pane.zoom_by(1.0)             # already clamped


def test_pan_time_clamps_at_first_candle_allows_overpan_past_last():
    """Drag-right (positive px) clamps at the first candle; drag-left
    (negative px) is allowed to over-pan up to half a viewport past the
    last candle so the user can see empty space to the right."""
    pane = EnginePane.new(0, "NQ", Timeframe.M15, 800.0, 400.0)
    candles = [_c(i, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
    pane.rebuild(candles)
    assert pane.time.base_index == 9

    # Drag right: clamp at first candle.
    pane.pan_time_by_px(1000.0)
    assert pane.time.base_index == 0

    # Drag left far: cap at last_idx + visible/2.
    # bar_width=8, width=800 → visible = ceil(800/8) = 100 bars
    # max_idx = 9 + 100 // 2 = 59
    pane.pan_time_by_px(-10_000.0)
    assert pane.time.base_index == 59
