"""Decode Databento .dbn.zst files via the official `databento` SDK."""

from __future__ import annotations

from pathlib import Path

import databento as db
from tqdm import tqdm

from core.candle import Candle


_NANOS_PER_SEC: int = 1_000_000_000
_PRICE_SCALE:   int = 1_000_000_000   # Databento ships fixed-point integers


def read_m1_bars(dbn_dir: Path, *, progress: bool = False) -> list[Candle]:
    """Decode every `.dbn.zst` in `dbn_dir`, return sorted-by-ts M1 bars.

    Matches magsi's databento.rs:
    - ts_event nanoseconds → seconds (// 1e9)
    - prices: 1e-9 fixed-point integers → float (/ 1e9)
    """
    files = sorted(dbn_dir.glob("*.dbn.zst"))
    bars: list[Candle] = []

    iterable = tqdm(files, desc="decoding DBN") if progress else files
    for path in iterable:
        store = db.DBNStore.from_file(path)
        for rec in store:
            bars.append(Candle(
                ts     = rec.ts_event // _NANOS_PER_SEC,
                open   = rec.open  / _PRICE_SCALE,
                high   = rec.high  / _PRICE_SCALE,
                low    = rec.low   / _PRICE_SCALE,
                close  = rec.close / _PRICE_SCALE,
                volume = int(rec.volume),
            ))

    bars.sort(key=lambda c: c.ts)
    return bars
