from core.candle import ALL, Candle, Timeframe, row_group_index


def test_candle_is_frozen():
    c = Candle(ts=0, open=1.0, high=2.0, low=0.5, close=1.5, volume=100)
    assert c.ts == 0
    assert c.close == 1.5
    try:
        c.ts = 1  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Candle should be frozen")


def test_timeframe_seconds():
    assert Timeframe.M1.seconds == 60
    assert Timeframe.M15.seconds == 900
    assert Timeframe.H1.seconds == 3_600
    assert Timeframe.H4.seconds == 14_400
    assert Timeframe.D1.seconds == 86_400
    assert Timeframe.W1.seconds == 604_800


def test_timeframe_str():
    assert str(Timeframe.M1) == "1m"
    assert str(Timeframe.M15) == "15m"
    assert str(Timeframe.H1) == "1h"
    assert str(Timeframe.D1) == "1d"
    assert str(Timeframe.W1) == "1w"


def test_timeframe_from_str_round_trip():
    for tf in Timeframe:
        assert Timeframe.from_str(str(tf)) is tf


def test_timeframe_from_str_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        Timeframe.from_str("99x")


def test_all_contains_every_variant_exactly_once():
    seen = set()
    for tf in ALL:
        assert tf not in seen, f"duplicate in ALL: {tf}"
        seen.add(tf)
    assert seen == set(Timeframe)


def test_row_group_index_round_trip():
    for tf in ALL:
        assert ALL[row_group_index(tf)] is tf


def test_row_group_index_specific_positions():
    assert row_group_index(Timeframe.M1) == 0
    assert row_group_index(Timeframe.M15) == 10
    assert row_group_index(Timeframe.M30) == 11
    assert row_group_index(Timeframe.H4) == 15
    assert row_group_index(Timeframe.W1) == len(ALL) - 1
