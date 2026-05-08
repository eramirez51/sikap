"""Parquet reader. Reads exactly one row group per call (the one for `tf`)
to avoid loading other timeframes' data."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from core.candle import Candle, Timeframe, row_group_index


SENTINEL_TS: int = -(2**63)   # i64::MIN — empty-TF marker. Reader filters out.

# Schema is defined here so storage and datasmith.write share one source.
SCHEMA: pa.Schema = pa.schema([
    pa.field("ts",        pa.int64(),   nullable=False),
    pa.field("timeframe", pa.string(),  nullable=False),
    pa.field("open",      pa.float64(), nullable=False),
    pa.field("high",      pa.float64(), nullable=False),
    pa.field("low",       pa.float64(), nullable=False),
    pa.field("close",     pa.float64(), nullable=False),
    pa.field("volume",    pa.int64(),   nullable=False),
])


def read_candles(
    path: Path,
    tf: Timeframe,
    from_ts: int,
    to_ts: int,
) -> list[Candle]:
    """Read the row group for `tf`, filter by [from_ts, to_ts] inclusive,
    skip ts == SENTINEL_TS rows, return sorted by ts ascending."""
    pf = pq.ParquetFile(path)
    table = pf.read_row_group(row_group_index(tf))
    ts_col     = table.column("ts").to_pylist()
    open_col   = table.column("open").to_pylist()
    high_col   = table.column("high").to_pylist()
    low_col    = table.column("low").to_pylist()
    close_col  = table.column("close").to_pylist()
    volume_col = table.column("volume").to_pylist()

    out: list[Candle] = []
    for i, ts in enumerate(ts_col):
        if ts == SENTINEL_TS:
            continue
        if ts < from_ts or ts > to_ts:
            continue
        out.append(Candle(
            ts=ts,
            open=open_col[i],
            high=high_col[i],
            low=low_col[i],
            close=close_col[i],
            volume=volume_col[i],
        ))
    out.sort(key=lambda c: c.ts)
    return out
