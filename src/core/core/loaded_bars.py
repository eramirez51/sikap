"""LoadedBars — per-symbol, per-timeframe candle cache.

Owns bar storage for a session; knows nothing about the replay cursor
or about parquet I/O. The session loads parquet rows in and inserts
them here; ReplayBuffer reads from here.
"""

from __future__ import annotations

from core.candle import Candle, Timeframe


class LoadedBars:
    """Loaded candle buffers keyed by (symbol, timeframe)."""

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
