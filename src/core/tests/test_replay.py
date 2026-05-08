from core.candle import Candle, Timeframe
from core.replay import LoadedBars, ReplayBuffer, aggregate_forming, bsearch


def _c(ts, close=1.0):
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=0)


# ── bsearch ─────────────────────────────────────────────────────────

def test_bsearch_empty_returns_none():
    assert bsearch([], 100) is None


def test_bsearch_target_before_first_returns_none():
    assert bsearch([_c(10), _c(20)], 5) is None


def test_bsearch_returns_last_index_le_target():
    buf = [_c(10), _c(20), _c(30), _c(40)]
    assert bsearch(buf, 25) == 1
    assert bsearch(buf, 30) == 2
    assert bsearch(buf, 1000) == 3


# ── LoadedBars ─────────────────────────────────────────────────────────

def test_loaded_bars_get_returns_inserted():
    lb = LoadedBars()
    assert lb.get("ES", Timeframe.M15) is None
    lb.insert("ES", Timeframe.M15, [_c(0), _c(900)])
    assert len(lb.get("ES", Timeframe.M15)) == 2
    assert lb.is_loaded("ES", Timeframe.M15)
    assert not lb.is_loaded("ES", Timeframe.H4)


def test_loaded_bars_last_ts_and_loaded_tfs():
    lb = LoadedBars()
    lb.insert("NQ", Timeframe.M15, [_c(0), _c(900), _c(1800)])
    lb.insert("NQ", Timeframe.H4, [_c(0)])
    assert lb.last_ts("NQ", Timeframe.M15) == 1800
    assert lb.last_ts("NQ", Timeframe.H4) == 0
    assert lb.last_ts("NQ", Timeframe.D1) is None
    tfs = sorted(lb.loaded_tfs("NQ"), key=lambda t: t.seconds)
    assert tfs == [Timeframe.M15, Timeframe.H4]


def test_loaded_bars_extend_appends():
    lb = LoadedBars()
    lb.insert("ES", Timeframe.M15, [_c(0)])
    lb.extend("ES", Timeframe.M15, [_c(900), _c(1800)])
    assert len(lb.get("ES", Timeframe.M15)) == 3


def test_loaded_bars_extend_noop_when_unloaded():
    lb = LoadedBars()
    lb.extend("ES", Timeframe.M15, [_c(0)])
    assert lb.get("ES", Timeframe.M15) is None


# ── aggregate_forming ─────────────────────────────────────────────────

def test_aggregate_forming_takes_open_from_first_close_from_last():
    bars = [
        Candle(ts=0,   open=10, high=11, low=9,  close=10.5, volume=1),
        Candle(ts=60,  open=10.5, high=12, low=10, close=11.5, volume=2),
        Candle(ts=120, open=11.5, high=13, low=11, close=12.0, volume=3),
    ]
    out = aggregate_forming(bars, 0, 120)
    assert out is not None
    assert out.open == 10
    assert out.high == 13
    assert out.low == 9
    assert out.close == 12.0
    assert out.volume == 6
    assert out.ts == 0


def test_aggregate_forming_returns_none_for_empty_range():
    bars = [Candle(ts=0, open=1, high=1, low=1, close=1, volume=0)]
    # to_ts before any bar
    assert aggregate_forming(bars, 1000, 999) is None


# ── ReplayBuffer.get_bars ─────────────────────────────────────────────

def test_get_bars_returns_completed_when_tf_le_stepping():
    lb = LoadedBars()
    bars = [_c(0), _c(900), _c(1800)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=900, stepping_tf=Timeframe.M15)
    assert len(rb.get_bars(Timeframe.M15)) == 2


def test_step_forward_advances_one_tf_bar():
    lb = LoadedBars()
    bars = [_c(0), _c(900), _c(1800), _c(2700)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=899, stepping_tf=Timeframe.M15)
    # cursor at 899 (one second before first bar's end ts 0+900-1=899)
    rb.step(1)
    # next bar's end ts = 900 + 900 - 1 = 1799
    assert rb.replay_ts == 1799
