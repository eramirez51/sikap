"""Parquet reader. Reads exactly one row group per call (the one for `tf`)
to avoid loading other timeframes' data. The on-disk contract (schema +
sentinel) lives in `core.parquet_spec` — both reader and writer import
from there."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from core.candle import Candle, Timeframe, row_group_index
from core.parquet_spec import SENTINEL_TS


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
