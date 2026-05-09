"""Canonical parquet writer. One row group per Timeframe in `core.candle.ALL`
order. Empty TFs get a single sentinel row (ts = SENTINEL_TS) so the row-group
indices stay aligned with `core.candle.row_group_index`."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from core.candle import ALL, Candle, Timeframe
from core.parquet_spec import SCHEMA, SENTINEL_TS


def write_parquet(
    path: Path,
    by_tf: dict[Timeframe, list[Candle]],
    *,
    progress: bool = False,
) -> None:
    """Write canonical parquet to `path`. Schema and sentinel are imported
    from `core.storage` so writer and reader cannot drift."""
    sentinel = [Candle(ts=SENTINEL_TS, open=0.0, high=0.0, low=0.0, close=0.0, volume=0)]
    iterable = tqdm(ALL, desc="writing TFs") if progress else ALL
    with pq.ParquetWriter(path, SCHEMA, compression="snappy") as writer:
        for tf in iterable:
            candles = by_tf.get(tf) or sentinel
            batch = pa.RecordBatch.from_pydict({
                "ts":        [c.ts        for c in candles],
                "timeframe": [str(tf)]    * len(candles),
                "open":      [c.open      for c in candles],
                "high":      [c.high      for c in candles],
                "low":       [c.low       for c in candles],
                "close":     [c.close     for c in candles],
                "volume":    [c.volume    for c in candles],
            }, schema=SCHEMA)
            # Force exactly one row group per TF. pyarrow's default caps a
            # row group at ~1M rows and silently splits anything larger,
            # which would shift every later TF's row-group index — readers
            # would silently fetch the wrong timeframe.
            writer.write_batch(batch, row_group_size=max(batch.num_rows, 1))
