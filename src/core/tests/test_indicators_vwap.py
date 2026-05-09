import math
from datetime import datetime, timezone

import pytest

from core.candle import Candle
from core.indicators.vwap import (
    VwapResult,
    compute_vwap,
    globex_daily_anchor,
    rth_us_anchor,
    utc_daily_anchor,
    vwap_overlay,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def test_empty_input_returns_empty_result():
    r = compute_vwap([])
    assert r.n == 0
    assert len(r.vwap) == 0
    assert len(r.lower) == 3   # default k_bands has 3 entries


def test_single_bar_vwap_equals_typical_price_and_std_zero():
    c = Candle(ts=_utc(2026, 5, 12, 14), open=100, high=102, low=99, close=101, volume=10)
    r = compute_vwap([c], anchor=utc_daily_anchor)
    tp = (102 + 99 + 101) / 3
    assert r.vwap[0] == pytest.approx(tp)
    assert r.std[0] == 0.0
    for k_idx in range(len(r.k_bands)):
        assert r.lower[k_idx][0] == pytest.approx(tp)
        assert r.upper[k_idx][0] == pytest.approx(tp)


def test_two_bar_volume_weighted_vwap_and_std():
    # bar 1: tp=100, vol=10  → Σpv=1000,  Σv=10, Σp²v=100,000
    # bar 2: tp=110, vol=20  → Σpv=3200,  Σv=30, Σp²v=342,000
    # vwap = 3200/30 = 106.6667; var = 342000/30 − vwap² = 22.2222; std ≈ 4.7140
    c1 = Candle(ts=_utc(2026, 5, 12, 14, 0), open=99,  high=101, low=99,  close=100, volume=10)
    c2 = Candle(ts=_utc(2026, 5, 12, 14, 1), open=109, high=111, low=109, close=110, volume=20)
    r = compute_vwap([c1, c2], anchor=utc_daily_anchor, k_bands=(1.0,))
    assert r.vwap[0] == pytest.approx(100.0)
    assert r.std[0]  == pytest.approx(0.0)
    assert r.vwap[1] == pytest.approx(3200 / 30, rel=1e-12)
    assert r.std[1]  == pytest.approx(math.sqrt(22.222222222222), rel=1e-9)


def test_anchor_change_resets_running_sums():
    a = _utc(2026, 5, 12, 12)  # day 1
    b = _utc(2026, 5, 13, 12)  # day 2 → utc_daily_anchor differs
    c1 = Candle(ts=a, open=100, high=100, low=100, close=100, volume=10)
    c2 = Candle(ts=b, open=200, high=200, low=200, close=200, volume=10)
    r = compute_vwap([c1, c2], anchor=utc_daily_anchor)
    assert r.vwap[0] == pytest.approx(100.0)
    assert r.vwap[1] == pytest.approx(200.0)  # new session — not 150


def test_session_starts_marks_anchor_changes():
    # Three bars: c1, c2 same UTC day; c3 next UTC day.
    c1 = Candle(ts=_utc(2026, 5, 12,  9), open=100, high=100, low=100, close=100, volume=10)
    c2 = Candle(ts=_utc(2026, 5, 12, 10), open=110, high=110, low=110, close=110, volume=10)
    c3 = Candle(ts=_utc(2026, 5, 13,  9), open=200, high=200, low=200, close=200, volume=10)
    r = compute_vwap([c1, c2, c3], anchor=utc_daily_anchor)
    assert list(r.session_starts) == [0, 2]   # c1 starts session 1, c3 starts session 2


def test_zero_volume_bar_contributes_nothing():
    c1 = Candle(ts=_utc(2026, 5, 12, 14, 0), open=100, high=100, low=100, close=100, volume=0)
    c2 = Candle(ts=_utc(2026, 5, 12, 14, 1), open=110, high=110, low=110, close=110, volume=10)
    r = compute_vwap([c1, c2], anchor=utc_daily_anchor)
    assert r.vwap[0] == pytest.approx(100.0)  # fallback to tp
    assert r.std[0]  == 0.0
    assert r.vwap[1] == pytest.approx(110.0)  # bar 2 drives the running vwap


def test_k_bands_emits_pair_per_multiplier_in_order():
    c1 = Candle(ts=_utc(2026, 5, 12, 14, 0), open=99,  high=101, low=99,  close=100, volume=10)
    c2 = Candle(ts=_utc(2026, 5, 12, 14, 1), open=109, high=111, low=109, close=110, volume=20)
    r = compute_vwap([c1, c2], anchor=utc_daily_anchor, k_bands=(1.0, 1.75, 2.0))
    assert len(r.lower) == 3
    assert len(r.upper) == 3
    vwap, std = r.vwap[1], r.std[1]
    for k_idx, k in enumerate((1.0, 1.75, 2.0)):
        assert r.lower[k_idx][1] == pytest.approx(vwap - k * std)
        assert r.upper[k_idx][1] == pytest.approx(vwap + k * std)


def test_custom_anchor_falls_back_to_python_path():
    """A user-defined anchor (not one of the built-ins) still works via
    the Python fallback."""
    def midnight_utc_offset_3h(ts: int) -> int:
        # New session every day at 03:00 UTC.
        offset = 3 * 3600
        return ((ts - offset) // 86400) * 86400 + offset

    c1 = Candle(ts=_utc(2026, 5, 12, 4),  open=100, high=100, low=100, close=100, volume=10)
    c2 = Candle(ts=_utc(2026, 5, 13, 2),  open=200, high=200, low=200, close=200, volume=10)
    c3 = Candle(ts=_utc(2026, 5, 13, 4),  open=300, high=300, low=300, close=300, volume=10)
    r = compute_vwap([c1, c2, c3], anchor=midnight_utc_offset_3h, k_bands=(1.0,))
    # c1 and c2 share an anchor (03:00 UTC on 2026-05-12); c3 starts a new one.
    assert r.vwap[0] == pytest.approx(100.0)
    assert r.vwap[1] == pytest.approx(150.0)   # (100·10 + 200·10) / 20
    assert r.vwap[2] == pytest.approx(300.0)   # new session


# ── built-in anchor functions (Python helpers) ───────────────────────


def test_globex_daily_anchor_pre_18_et_belongs_to_prior_session():
    ts = _utc(2026, 5, 12, 21)              # 17:00 EDT
    expected = _utc(2026, 5, 11, 22)        # 18:00 EDT prior day
    assert globex_daily_anchor(ts) == expected


def test_globex_daily_anchor_at_18_et_starts_new_session():
    ts = _utc(2026, 5, 12, 22)              # 18:00 EDT
    assert globex_daily_anchor(ts) == ts


def test_globex_daily_anchor_handles_dst_winter():
    ts = _utc(2026, 1, 15, 22)              # 17:00 EST (UTC-5)
    expected = _utc(2026, 1, 14, 23)        # 18:00 EST prior day = 23:00 UTC
    assert globex_daily_anchor(ts) == expected


def test_rth_us_anchor_pre_0930_belongs_to_prior_day():
    ts = _utc(2026, 5, 12, 13)              # 09:00 EDT
    expected = _utc(2026, 5, 11, 13, 30)    # 09:30 EDT prior day
    assert rth_us_anchor(ts) == expected


def test_rth_us_anchor_at_0930_starts_new_session():
    ts = _utc(2026, 5, 12, 13, 30)          # 09:30 EDT
    assert rth_us_anchor(ts) == ts


def test_utc_daily_anchor_truncates_to_midnight():
    ts = _utc(2026, 5, 12, 14, 30)
    assert utc_daily_anchor(ts) == _utc(2026, 5, 12, 0, 0)


# ── overlay builder ──────────────────────────────────────────────────


def test_vwap_overlay_empty_result_returns_empty_overlay():
    r = compute_vwap([])
    o = vwap_overlay(r)
    assert o.name == "vwap"
    assert o.polylines == ()


def test_vwap_overlay_emits_center_plus_two_per_band_per_session():
    # Single-session window: 1 center + 2 (upper/lower) × 3 bands = 7
    bars = [
        Candle(ts=_utc(2026, 5, 12, 14, 0), open=99,  high=101, low=99,  close=100, volume=10),
        Candle(ts=_utc(2026, 5, 12, 14, 1), open=109, high=111, low=109, close=110, volume=20),
    ]
    r = compute_vwap(bars, anchor=utc_daily_anchor, k_bands=(1.0, 1.75, 2.0))
    o = vwap_overlay(r)
    assert len(o.polylines) == 7


def test_vwap_overlay_polylines_span_all_bars_across_sessions():
    # Polylines stay connected across session boundaries — at a reset
    # the band pinches toward the new session's first typical price.
    # Matches TV's continuous look. session_starts is still tracked on
    # the result for downstream code that wants to mark the boundary.
    bars = [
        Candle(ts=_utc(2026, 5, 12,  9), open=100, high=100, low=100, close=100, volume=10),
        Candle(ts=_utc(2026, 5, 12, 10), open=110, high=110, low=110, close=110, volume=10),
        Candle(ts=_utc(2026, 5, 13,  9), open=200, high=200, low=200, close=200, volume=10),
        Candle(ts=_utc(2026, 5, 13, 10), open=210, high=210, low=210, close=210, volume=10),
    ]
    r = compute_vwap(bars, anchor=utc_daily_anchor, k_bands=(1.0,))
    o = vwap_overlay(r)
    # 1 center + 2 (upper/lower) × 1 band = 3 polylines, NOT 6.
    assert len(o.polylines) == 3
    assert list(o.polylines[0].xs) == [0.0, 1.0, 2.0, 3.0]   # spans both sessions
    assert list(r.session_starts)  == [0, 2]                  # still recorded


def test_vwap_overlay_xs_are_bar_indices():
    bars = [
        Candle(ts=_utc(2026, 5, 12, 14, 0), open=99,  high=101, low=99,  close=100, volume=10),
        Candle(ts=_utc(2026, 5, 12, 14, 1), open=109, high=111, low=109, close=110, volume=20),
    ]
    r = compute_vwap(bars, anchor=utc_daily_anchor, k_bands=(1.0,))
    o = vwap_overlay(r)
    center = o.polylines[0]
    assert list(center.xs) == [0.0, 1.0]
    assert center.ys[0] == pytest.approx(r.vwap[0])
    assert center.ys[1] == pytest.approx(r.vwap[1])


def test_vwap_overlay_band_polylines_track_band_pairs():
    bars = [
        Candle(ts=_utc(2026, 5, 12, 14, 0), open=99,  high=101, low=99,  close=100, volume=10),
        Candle(ts=_utc(2026, 5, 12, 14, 1), open=109, high=111, low=109, close=110, volume=20),
    ]
    r = compute_vwap(bars, anchor=utc_daily_anchor, k_bands=(1.0, 2.0))
    o = vwap_overlay(r)
    # polylines[1] = upper(k=1.0); polylines[2] = lower(k=1.0)
    # polylines[3] = upper(k=2.0); polylines[4] = lower(k=2.0)
    assert o.polylines[1].ys[1] == pytest.approx(r.upper[0][1])
    assert o.polylines[2].ys[1] == pytest.approx(r.lower[0][1])
    assert o.polylines[3].ys[1] == pytest.approx(r.upper[1][1])
    assert o.polylines[4].ys[1] == pytest.approx(r.lower[1][1])
