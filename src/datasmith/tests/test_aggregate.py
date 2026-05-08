from core.candle import Candle, Timeframe
from datasmith.aggregate import aggregate


def _c(ts, o, h, l, cl, v=1):
    return Candle(ts=ts, open=o, high=h, low=l, close=cl, volume=v)


def test_aggregate_buckets_m1_into_h1():
    # 60 M1 bars of 1 hour, all in one H1 bucket.
    src = [_c(i * 60, 100, 100 + i, 100 - i, 100, v=1) for i in range(60)]
    out = aggregate(src, Timeframe.H1)
    assert len(out) == 1
    bar = out[0]
    assert bar.ts == 0
    assert bar.open == 100
    assert bar.high == 100 + 59
    assert bar.low == 100 - 59
    assert bar.close == 100
    assert bar.volume == 60


def test_aggregate_advances_to_next_bucket():
    # 2 hours of M1 bars (120) → 2 H1 buckets.
    src = [_c(i * 60, 100, 100 + (i % 60), 100 - (i % 60), 100, v=1) for i in range(120)]
    out = aggregate(src, Timeframe.H1)
    assert len(out) == 2
    assert out[0].ts == 0
    assert out[1].ts == 3600


def test_weekly_aligns_to_monday():
    # 2026-01-05 (Mon) 00:00 UTC = 1767571200
    monday = 1767571200
    # bars on Mon, Tue, Wed, Thu — should all bucket to Monday's ts
    src = [_c(monday + i * 86400, 1, 2, 0.5, 1.5) for i in range(4)]
    out = aggregate(src, Timeframe.W1)
    assert len(out) == 1
    assert out[0].ts == monday


def test_aggregate_empty_returns_empty():
    assert aggregate([], Timeframe.H1) == []
