import pytest

from core.candle import Candle
from core.indicators.volume_profile import (
    compute_volume_profile,
    hvn_overlay,
    hvn_ranges,
)


def _bar(ts, low, high, volume):
    # OHLC body inside [low, high]; HVN math only uses low/high/volume.
    return Candle(ts=ts, open=low, high=high, low=low, close=high, volume=volume)


# ── compute_volume_profile ───────────────────────────────────────────


def test_empty_input_returns_none():
    assert compute_volume_profile([]) is None


def test_flat_window_returns_none():
    bars = [_bar(0, 100, 100, 10), _bar(60, 100, 100, 10)]
    assert compute_volume_profile(bars, window=2, n_bins=10) is None


def test_single_bar_volume_spreads_across_touched_bins():
    # 10 bins over [100, 110]; bar [104, 105] touches bins 4..5 (volume
    # split uniformly = 25 each).
    bars = [_bar(-60, 100, 100, 0), _bar(0, 110, 110, 0),
            _bar(60, 104, 105, 50)]
    p = compute_volume_profile(bars, window=3, n_bins=10)
    assert p is not None
    assert p.bin_volumes[4] == pytest.approx(25.0)
    assert p.bin_volumes[5] == pytest.approx(25.0)
    assert sum(p.bin_volumes) == pytest.approx(50.0)


def test_window_clips_to_last_n_bars():
    # 5 bars; window=2 should only use bars[3] and bars[4].
    bars = [
        _bar(0,  100, 100, 999),   # excluded
        _bar(60, 100, 100, 999),   # excluded
        _bar(120, 100, 100, 999),  # excluded
        _bar(180, 100, 110, 30),   # included
        _bar(240, 100, 110, 30),   # included
    ]
    p = compute_volume_profile(bars, window=2, n_bins=10)
    assert p is not None
    assert sum(p.bin_volumes) == pytest.approx(60.0)   # 30 + 30 only
    assert p.window_first == 3
    assert p.window_last  == 4


def test_zero_volume_bars_contribute_nothing():
    bars = [_bar(0, 100, 110, 0), _bar(60, 100, 110, 0), _bar(120, 100, 110, 100)]
    p = compute_volume_profile(bars, window=3, n_bins=10)
    assert p is not None
    assert sum(p.bin_volumes) == pytest.approx(100.0)


# ── hvn_ranges ────────────────────────────────────────────────────────


def test_hvn_empty_profile_returns_no_ranges():
    bars = [_bar(0, 100, 110, 0)]
    p = compute_volume_profile(bars, window=1, n_bins=10)
    assert p is None or hvn_ranges(p) == []


def test_hvn_threshold_picks_above_average_bins():
    # Force a known histogram: heavy bar in [105,106], light bars elsewhere.
    bars = [
        _bar(0,  100, 101, 1),
        _bar(60, 102, 103, 1),
        _bar(120, 105, 106, 100),   # this bin should be HVN
        _bar(180, 108, 109, 1),
    ]
    p = compute_volume_profile(bars, window=4, n_bins=10)
    assert p is not None
    ranges = hvn_ranges(p, threshold=1.6)
    # Heavy bar straddled bin 5..6. Both should clear 1.6 × mean.
    assert any(lo <= 105.5 <= hi for lo, hi in ranges)


def test_hvn_merges_contiguous_bins():
    # Anchor the price range to [100, 110] with zero-volume edge bars,
    # then dump heavy volume in [103, 106] across many bars so bins 3..5
    # all clear the threshold and merge into a single range.
    bars = [_bar(-60, 100, 100, 0)]                                  # range floor
    bars += [_bar(i * 60, 103, 106, 100) for i in range(10)]         # heavy
    bars += [_bar(700, 110, 110, 0)]                                 # range ceiling
    p = compute_volume_profile(bars, window=len(bars), n_bins=10)
    assert p is not None
    ranges = hvn_ranges(p, threshold=1.6)
    assert len(ranges) == 1
    lo, hi = ranges[0]
    # bar high=106 lands exactly on the boundary of bin 6 ([106,107)) so
    # `int()` floor puts contribution into bin 6 too — HVN range extends
    # to the *upper* edge of bin 6.
    assert lo == pytest.approx(103.0)
    assert hi == pytest.approx(107.0)


# ── hvn_overlay ───────────────────────────────────────────────────────


def test_hvn_overlay_none_profile_returns_empty_overlay():
    o = hvn_overlay(None)
    assert o.name == "hvn"
    assert o.rects == ()


def test_hvn_overlay_emits_one_rect_per_range():
    bars = [_bar(-60, 100, 100, 0)]
    bars += [_bar(i * 60, 103, 106, 100) for i in range(10)]
    bars += [_bar(700, 110, 110, 0)]
    p = compute_volume_profile(bars, window=len(bars), n_bins=10)
    o = hvn_overlay(p)
    assert len(o.rects) == 1
    r = o.rects[0]
    assert r.x1 == 0.0
    assert r.x2 == float(len(bars) - 1)
