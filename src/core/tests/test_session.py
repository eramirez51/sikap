import pyarrow as pa
import pyarrow.parquet as pq

from core.candle import ALL, Candle, Timeframe
from core.session import Session
from core.storage import SCHEMA, SENTINEL_TS


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


def _bars(start_ts, count, step):
    return [
        Candle(ts=start_ts + i * step, open=1, high=2, low=0.5, close=1.5, volume=10)
        for i in range(count)
    ]


def test_open_sets_replay_ts_to_first_bar_end(tmp_path):
    _write(tmp_path / "ES.parquet", {Timeframe.M15: _bars(0, 10, 900)})
    s = Session(tmp_path)
    s.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    assert s.replay_ts == 899   # first bar ts 0 + tf_secs 900 - 1
    assert s.stepping_tf is Timeframe.M15


def test_step_forward_advances_one_bar(tmp_path):
    _write(tmp_path / "ES.parquet", {Timeframe.M15: _bars(0, 10, 900)})
    s = Session(tmp_path)
    s.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    s.step(1)
    assert s.replay_ts == 900 + 900 - 1


def test_step_backward(tmp_path):
    _write(tmp_path / "ES.parquet", {Timeframe.M15: _bars(0, 10, 900)})
    s = Session(tmp_path)
    s.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    s.step(1); s.step(1)
    assert s.replay_ts == 2 * 900 + 899
    s.step(-1)
    assert s.replay_ts == 1 * 900 + 899


def test_bars_lazy_loads(tmp_path):
    _write(tmp_path / "ES.parquet", {
        Timeframe.M15: _bars(0, 10, 900),
        Timeframe.H4:  _bars(0, 5, 14400),
    })
    s = Session(tmp_path)
    s.open("ES", Timeframe.M15, range_from=0, range_to=2**63 - 1)
    # H4 not loaded until requested
    assert s.bars("ES", Timeframe.H4)        # triggers ensure_tf
