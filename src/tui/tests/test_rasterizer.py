from core.candle import Candle, Timeframe
from core.engine.pane import EnginePane

from tui.rasterizer import Rasterizer


def _c(ts, o, h, l, cl):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=0)


def test_render_returns_rgba_bytes_of_correct_size():
    pane = EnginePane.new(0, "ES", Timeframe.M15, 200.0, 100.0)
    pane.rebuild([_c(i, 100, 105, 99, 102 + (i % 3 - 1)) for i in range(10)])

    r = Rasterizer(200, 100)
    out = r.render(pane)
    assert len(out) == 200 * 100 * 4


def test_render_paints_something():
    """After rendering 10 candles, the buffer should not be uniform background."""
    pane = EnginePane.new(0, "ES", Timeframe.M15, 200.0, 100.0)
    pane.rebuild([_c(i, 100, 105, 99, 102 + (i % 3 - 1)) for i in range(10)])

    r = Rasterizer(200, 100)
    out = r.render(pane)
    # Sample a few middle pixels — expect at least one differs from the bg.
    bg = bytes((20, 20, 28, 255))
    middle_offset = (50 * 200 + 100) * 4
    sample = out[middle_offset : middle_offset + 400]
    assert any(sample[i:i+4] != bg for i in range(0, len(sample), 4))


def test_resize_changes_buffer_size():
    r = Rasterizer(100, 50)
    pane = EnginePane.new(0, "ES", Timeframe.M15, 100.0, 50.0)
    pane.rebuild([_c(0, 1, 2, 0.5, 1.5)])
    assert len(r.render(pane)) == 100 * 50 * 4
    r.resize(200, 100)
    pane.resize(200.0, 100.0)
    pane.rebuild([_c(0, 1, 2, 0.5, 1.5)])
    assert len(r.render(pane)) == 200 * 100 * 4
