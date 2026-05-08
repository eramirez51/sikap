from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from core.candle import ALL, Candle, Timeframe, row_group_index
from core.storage import SCHEMA, SENTINEL_TS, read_candles


def _write_canonical(path: Path, by_tf: dict[Timeframe, list[Candle]]) -> None:
    """Helper that mirrors what datasmith.write will do — used here only to
    exercise core.storage. The real writer lives in datasmith."""
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


def test_read_candles_returns_only_requested_tf(tmp_path):
    path = tmp_path / "ES.parquet"
    h4 = [Candle(ts=i * 14400, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(3)]
    d1 = [Candle(ts=i * 86400, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(2)]
    _write_canonical(path, {Timeframe.H4: h4, Timeframe.D1: d1})

    got = read_candles(path, Timeframe.H4, 0, 2**63 - 1)
    assert len(got) == 3
    assert all(c.ts in {0, 14400, 28800} for c in got)


def test_read_candles_skips_sentinel_rows(tmp_path):
    path = tmp_path / "X.parquet"
    _write_canonical(path, {})       # all TFs are sentinels
    for tf in ALL:
        assert read_candles(path, tf, 0, 2**63 - 1) == []


def test_read_candles_filters_by_range(tmp_path):
    path = tmp_path / "ES.parquet"
    h4 = [Candle(ts=i * 14400, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(5)]
    _write_canonical(path, {Timeframe.H4: h4})

    got = read_candles(path, Timeframe.H4, from_ts=14400, to_ts=43200)
    assert [c.ts for c in got] == [14400, 28800, 43200]


def test_read_candles_returns_sorted_by_ts(tmp_path):
    path = tmp_path / "ES.parquet"
    candles = [
        Candle(ts=20, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(ts=10, open=1, high=2, low=0.5, close=1.5, volume=10),
        Candle(ts=30, open=1, high=2, low=0.5, close=1.5, volume=10),
    ]
    _write_canonical(path, {Timeframe.M1: candles})

    got = read_candles(path, Timeframe.M1, 0, 2**63 - 1)
    assert [c.ts for c in got] == [10, 20, 30]


def test_read_candles_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_candles(tmp_path / "missing.parquet", Timeframe.M1, 0, 2**63 - 1)
