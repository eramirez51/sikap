import pyarrow as pa
import pyarrow.parquet as pq

from core.candle import ALL, Candle, Timeframe
from core.engine import AppEngine, classify_step
from core.engine.events import BarAppended, BarRemoved, BarUpdated
from core.parquet_spec import SCHEMA, SENTINEL_TS


def _write(path, by_tf):
    sentinel = [Candle(ts=SENTINEL_TS, open=0.0, high=0.0, low=0.0, close=0.0, volume=0)]
    with pq.ParquetWriter(path, SCHEMA, compression="snappy") as writer:
        for tf in ALL:
            cs = by_tf.get(tf) or sentinel
            batch = pa.RecordBatch.from_pydict({
                "ts":        [c.ts for c in cs],
                "timeframe": [str(tf)] * len(cs),
                "open":      [c.open for c in cs],
                "high":      [c.high for c in cs],
                "low":       [c.low for c in cs],
                "close":     [c.close for c in cs],
                "volume":    [c.volume for c in cs],
            }, schema=SCHEMA)
            writer.write_batch(batch)


def test_classify_step_branches():
    assert classify_step(10, 11, 7) == BarAppended(pane_id=7)
    assert classify_step(10,  9, 7) == BarRemoved(pane_id=7)
    assert classify_step(10, 10, 7) == BarUpdated(pane_id=7)


def test_app_engine_open_and_add_pane(tmp_path):
    bars = [Candle(ts=i * 900, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(10)]
    _write(tmp_path / "ES.parquet", {Timeframe.M15: bars})

    eng = AppEngine(tmp_path)
    eng.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    pid = eng.add_pane("ES", Timeframe.M15, width=800.0, height=400.0)
    assert eng.pane(pid) is not None
    assert eng.pane(pid).candles


def test_step_emits_event_per_pane(tmp_path):
    bars = [Candle(ts=i * 900, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(10)]
    _write(tmp_path / "ES.parquet", {Timeframe.M15: bars})

    eng = AppEngine(tmp_path)
    eng.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    pid1 = eng.add_pane("ES", Timeframe.M15, 800.0, 400.0)
    pid2 = eng.add_pane("ES", Timeframe.M15, 800.0, 400.0)

    events = eng.step(+1)
    assert len(events) == 2
    pane_ids = {ev.pane_id for ev in events}     # all are BarAppended at fwd step
    assert pane_ids == {pid1, pid2}
