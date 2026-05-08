"""Canonical parquet contract.

Both the writer (`datasmith.write.write_parquet`) and the reader
(`core.storage.read_candles`) import `SCHEMA` and `SENTINEL_TS` from
here. Neither side owns the contract; it owns itself.

Row-group order is `core.candle.ALL` — both sides iterate the same
tuple, so positions cannot drift.
"""

from __future__ import annotations

import pyarrow as pa


# i64::MIN — single-row marker for empty timeframes. Without it pyarrow
# elides the row group entirely and shifts every later row-group index.
SENTINEL_TS: int = -(2**63)

# Field order is fixed; readers select by column name, but the schema
# is part of the on-disk format and changes are breaking.
SCHEMA: pa.Schema = pa.schema([
    pa.field("ts",        pa.int64(),   nullable=False),
    pa.field("timeframe", pa.string(),  nullable=False),
    pa.field("open",      pa.float64(), nullable=False),
    pa.field("high",      pa.float64(), nullable=False),
    pa.field("low",       pa.float64(), nullable=False),
    pa.field("close",     pa.float64(), nullable=False),
    pa.field("volume",    pa.int64(),   nullable=False),
])
