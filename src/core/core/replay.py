"""Replay buffer + lazy candle storage. No I/O — `core.session` handles parquet."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Sequence

from core.candle import Candle, Timeframe


class LoadedBars:
    """Loaded candle buffers keyed by (symbol, timeframe). Owns bar storage;
    does not know about the replay cursor."""

    def __init__(self) -> None:
        self._inner: dict[str, dict[Timeframe, list[Candle]]] = {}

    def get(self, symbol: str, tf: Timeframe) -> list[Candle] | None:
        return self._inner.get(symbol, {}).get(tf)

    def is_loaded(self, symbol: str, tf: Timeframe) -> bool:
        return self.get(symbol, tf) is not None

    def last_ts(self, symbol: str, tf: Timeframe) -> int | None:
        buf = self.get(symbol, tf)
        return buf[-1].ts if buf else None

    def loaded_tfs(self, symbol: str) -> list[Timeframe]:
        return list(self._inner.get(symbol, {}).keys())

    def insert(self, symbol: str, tf: Timeframe, candles: list[Candle]) -> None:
        self._inner.setdefault(symbol, {})[tf] = candles

    def extend(self, symbol: str, tf: Timeframe, more: list[Candle]) -> None:
        existing = self._inner.get(symbol, {}).get(tf)
        if existing is None:
            return
        existing.extend(more)


def bsearch(buf: Sequence[Candle], target: int) -> int | None:
    """Last index i with buf[i].ts <= target, or None if buf empty or
    target < buf[0].ts."""
    if not buf or buf[0].ts > target:
        return None
    lo, hi = 0, len(buf)
    while lo < hi:
        mid = (lo + hi) // 2
        if buf[mid].ts <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1 if lo > 0 else None


def aggregate_forming(
    stepping_buf: Sequence[Candle],
    from_ts: int,
    to_ts: int,
) -> Candle | None:
    """Build a single forming candle by aggregating stepping-TF bars whose
    ts is in [from_ts, to_ts]. Returns None if no bars in range."""
    end = bsearch(stepping_buf, to_ts)
    if end is None:
        return None
    bars = [c for c in stepping_buf[: end + 1] if c.ts >= from_ts]
    if not bars:
        return None
    return Candle(
        ts     = from_ts,
        open   = bars[0].open,
        high   = max(c.high for c in bars),
        low    = min(c.low  for c in bars),
        close  = bars[-1].close,
        volume = sum(c.volume for c in bars),
    )


@dataclass
class ReplayBuffer:
    loaded:      LoadedBars
    symbol:      str
    replay_ts:   int
    stepping_tf: Timeframe

    def get_bars(self, tf: Timeframe) -> list[Candle]:
        buf = self.loaded.get(self.symbol, tf)
        if buf is None:
            return []

        idx = bsearch(buf, self.replay_ts)
        if idx is None:
            return []

        if tf.seconds <= self.stepping_tf.seconds:
            return list(buf[: idx + 1])

        # tf > stepping: build forming bar from stepping-TF bars
        completed = list(buf[:idx])
        current_bar_ts = buf[idx].ts

        stepping_buf = self.loaded.get(self.symbol, self.stepping_tf)
        if stepping_buf is None:
            return completed

        forming = aggregate_forming(stepping_buf, current_bar_ts, self.replay_ts)
        if forming is None:
            return completed
        return [*completed, forming]

    def step(self, direction: int) -> None:
        buf = self.loaded.get(self.symbol, self.stepping_tf)
        if not buf:
            return
        tf_sec = self.stepping_tf.seconds

        if direction > 0:
            # forward: first bar whose end ts >= replay_ts + tf_sec
            target = self.replay_ts + tf_sec
            for i, c in enumerate(buf):
                if c.ts + tf_sec - 1 >= target:
                    self.replay_ts = c.ts + tf_sec - 1
                    return
        else:
            # backward: last bar whose end ts < replay_ts
            target = self.replay_ts - tf_sec
            idx = bsearch(buf, target)
            if idx is not None:
                self.replay_ts = buf[idx].ts + tf_sec - 1

    def seek(self, ts: int) -> None:
        buf = self.loaded.get(self.symbol, self.stepping_tf)
        if not buf:
            return
        tf_sec = self.stepping_tf.seconds
        min_ts = buf[0].ts  + tf_sec - 1
        max_ts = buf[-1].ts + tf_sec - 1
        ts = max(min_ts, min(ts, max_ts))
        idx = bsearch(buf, ts)
        self.replay_ts = (buf[idx].ts + tf_sec - 1) if idx is not None else min_ts
