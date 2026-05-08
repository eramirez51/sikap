"""Session: lazy parquet loader + replay cursor + range window."""

from __future__ import annotations

from pathlib import Path

from core.candle import Candle, Timeframe
from core.loaded_bars import LoadedBars
from core.replay import ReplayBuffer, bsearch
from core.storage import read_candles


class Session:
    def __init__(self, data_dir: Path) -> None:
        self._buffer = ReplayBuffer(
            loaded      = LoadedBars(),
            symbol      = "",
            replay_ts   = 0,
            stepping_tf = Timeframe.M15,
        )
        self._data_dir   = Path(data_dir)
        self._range_from = 0
        self._range_to   = 2**63 - 1

    def open(self, symbol: str, stepping_tf: Timeframe,
             range_from: int, range_to: int) -> None:
        self._range_from         = range_from
        self._range_to           = range_to
        self._buffer.symbol      = symbol
        self._buffer.stepping_tf = stepping_tf

        self.ensure_tf(symbol, stepping_tf)

        tf_sec = stepping_tf.seconds
        buf = self._buffer.loaded.get(symbol, stepping_tf)
        if not buf:
            raise ValueError(f"no candles loaded for {symbol}/{stepping_tf}")

        idx = bsearch(buf, range_from)
        if idx is not None:
            self._buffer.replay_ts = buf[idx].ts + tf_sec - 1
        else:
            first = buf[0]
            self._buffer.replay_ts = first.ts + tf_sec - 1

    def ensure_tf(self, symbol: str, tf: Timeframe) -> None:
        if self._buffer.loaded.is_loaded(symbol, tf):
            return
        path = self._data_dir / f"{symbol}.parquet"
        candles = read_candles(path, tf, self._range_from, self._range_to)
        self._buffer.loaded.insert(symbol, tf, candles)

    def step(self, direction: int) -> None:
        self._buffer.step(direction)
        if direction > 0:
            self._maybe_extend()

    def seek(self, ts: int) -> None:
        self._buffer.seek(ts)

    def bars(self, symbol: str, tf: Timeframe) -> list[Candle]:
        try:
            self.ensure_tf(symbol, tf)
        except FileNotFoundError:
            return []
        return self._buffer.get_bars(tf)

    def set_stepping_tf(self, tf: Timeframe) -> None:
        self._buffer.stepping_tf = tf

    @property
    def replay_ts(self) -> int:
        return self._buffer.replay_ts

    @property
    def stepping_tf(self) -> Timeframe:
        return self._buffer.stepping_tf

    @property
    def symbol(self) -> str:
        return self._buffer.symbol

    def _maybe_extend(self) -> None:
        sym = self._buffer.symbol
        tf  = self._buffer.stepping_tf
        buf = self._buffer.loaded.get(sym, tf)
        if not buf:
            return
        last_ts = buf[-1].ts
        idx = bsearch(buf, self._buffer.replay_ts)
        progress = (idx if idx is not None else 0) / len(buf)
        if progress < 0.8 or last_ts >= self._range_to:
            return

        extend_from = last_ts + 1
        extend_to   = min(extend_from + 14 * 86400, self._range_to)
        path = self._data_dir / f"{sym}.parquet"
        for load_tf in self._buffer.loaded.loaded_tfs(sym):
            try:
                more = read_candles(path, load_tf, extend_from, extend_to)
            except FileNotFoundError:
                continue
            self._buffer.loaded.extend(sym, load_tf, more)
