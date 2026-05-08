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


# ── ReplayBuffer.step backward + clamp ────────────────────────────────

def test_step_backward_moves_to_previous_bar():
    lb = LoadedBars()
    bars = [_c(0), _c(900), _c(1800), _c(2700)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=1799, stepping_tf=Timeframe.M15)
    rb.step(-1)
    # Previous bar's end ts = 0 + 900 - 1 = 899
    assert rb.replay_ts == 899


def test_step_forward_clamps_at_last_bar():
    lb = LoadedBars()
    bars = [_c(0), _c(900)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=1799, stepping_tf=Timeframe.M15)
    rb.step(1)
    # Already at the last bar's end ts; cursor must not move past the end.
    assert rb.replay_ts == 1799


# ── ReplayBuffer.seek ─────────────────────────────────────────────────

def test_seek_snaps_to_bar_end_inside_range():
    lb = LoadedBars()
    bars = [_c(0), _c(900), _c(1800)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=0, stepping_tf=Timeframe.M15)
    rb.seek(1500)             # mid second bar
    assert rb.replay_ts == 1799   # snap to second bar's end ts


def test_seek_clamps_below_min_to_first_bar_end():
    lb = LoadedBars()
    bars = [_c(0), _c(900)]
    lb.insert("ES", Timeframe.M15, bars)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=900, stepping_tf=Timeframe.M15)
    rb.seek(-1000)
    assert rb.replay_ts == 899    # first bar's end ts


# ── ReplayBuffer.get_bars when tf > stepping (headline feature) ──────

def test_get_bars_builds_forming_bar_when_tf_above_stepping():
    """Stepping is M1 (60s). Chart TF is M15 (900s). The cursor is mid-period;
    get_bars(M15) must return completed M15 bars + a forming M15 bar built
    from the M1 bars in the current 15-minute period."""
    lb = LoadedBars()
    # M15 bars at ts=0 and ts=900. Cursor lands inside the second M15 period.
    m15 = [_c(0), _c(900)]
    # M1 bars covering 16 minutes (0..960). The second M15 period starts at 900;
    # M1 bars at 900, 960 are inside it (and partially built, since cursor is at 960).
    m1 = [Candle(ts=t, open=10.0 + i, high=10.0 + i + 0.5, low=10.0 + i - 0.5,
                 close=10.0 + i + 0.2, volume=1) for i, t in enumerate(range(0, 1020, 60))]
    lb.insert("ES", Timeframe.M15, m15)
    lb.insert("ES", Timeframe.M1,  m1)
    rb = ReplayBuffer(loaded=lb, symbol="ES", replay_ts=960, stepping_tf=Timeframe.M1)

    out = rb.get_bars(Timeframe.M15)
    # Completed M15 bars before current period: just the bar at ts=0.
    # Plus one forming bar covering the current M15 period (ts=900).
    assert len(out) == 2
    assert out[0].ts == 0
    forming = out[1]
    assert forming.ts == 900             # forming bar starts at current TF period start
    # Forming uses M1 bars at ts=900 and ts=960; open from first, close from last.
    assert forming.open == m1[15].open   # ts=900 → index 15 in m1
    assert forming.close == m1[16].close  # ts=960 → index 16
