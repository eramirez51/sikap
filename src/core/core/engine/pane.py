"""EnginePane: composes TimeAxis + PriceAxis + candle data for one pane."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Sequence

from core.candle import Candle, Timeframe
from core.chart.price_axis import PriceAxis
from core.chart.time_axis import TimeAxis


@dataclass
class EnginePane:
    id:      int
    symbol:  str
    tf:      Timeframe
    time:    TimeAxis
    price:   PriceAxis
    candles: list[Candle] = field(default_factory=list)

    @classmethod
    def new(cls, id: int, symbol: str, tf: Timeframe,
            width: float, height: float) -> "EnginePane":
        return cls(
            id=id, symbol=symbol, tf=tf,
            time  = TimeAxis(bar_width=8.0, base_index=0, width=width),
            price = PriceAxis.new(height),
            candles=[],
        )

    def rebuild(self, candles: Sequence[Candle]) -> None:
        if not candles:
            return
        self.candles = list(candles)
        self.time.base_index = len(candles) - 1
        self._refit_price_to_visible()

    def resize(self, width: float, height: float) -> None:
        self.time.resize(width)
        self.price.resize(height)
        self._refit_price_to_visible()

    def zoom_by(self, delta: float) -> bool:
        new_width = max(2.0, min(64.0, self.time.bar_width + delta))
        if abs(new_width - self.time.bar_width) < sys.float_info.epsilon:
            return False
        self.time.bar_width = new_width
        self._refit_price_to_visible()
        return True

    def pan_time_by_px(self, px: float) -> bool:
        if abs(px) < 0.5:
            return False
        bars = round(px / self.time.bar_width)
        if bars == 0:
            return False
        max_idx = max(0, len(self.candles) - 1)
        new_idx = max(0, min(max_idx, self.time.base_index - bars))
        if new_idx == self.time.base_index:
            return False
        self.time.base_index = new_idx
        self._refit_price_to_visible()
        return True

    def pan_price_by_px(self, px: float) -> bool:
        if abs(px) < 0.5:
            return False
        self.price.pan_by_px(px)
        return True

    def _refit_price_to_visible(self) -> None:
        if not self.candles:
            return
        last_idx = len(self.candles) - 1
        first, last = self.time.visible_range()
        first = min(first, last_idx)
        last  = min(last,  last_idx)
        visible = self.candles[first : last + 1]
        if visible:
            self.price.fit_to(visible)
