"""The y-axis as one module: price↔pixel mapping, fit policy, pan policy,
and the labels rendered into the price gutter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.candle import Candle
from core.chart.scene import TextPrim
from core.chart.ticks import price_ticks


_TARGET_TICKS:   int                              = 8
_FIT_PAD_FRAC:   float                            = 0.05
_LABEL_INSET_PX: float                            = 6.0
_LABEL_SIZE:     float                            = 11.0
_LABEL_COLOR:    tuple[float, float, float, float] = (0.78, 0.78, 0.82, 1.0)


@dataclass(frozen=True, slots=True)
class YProjection:
    """Linear price→pixel-y projection. Shared with the rasterizer/shader."""
    price_scale:  float
    price_offset: float


@dataclass
class PriceAxis:
    height:    float
    price_min: float
    price_max: float

    @classmethod
    def new(cls, height: float) -> "PriceAxis":
        return cls(height=height, price_min=0.0, price_max=1.0)

    def y_for_price(self, price: float) -> float:
        p = self.projection()
        return price * p.price_scale + p.price_offset

    def projection(self) -> YProjection:
        span = max(self.price_max - self.price_min, 1e-9)
        return YProjection(
            price_scale  = -self.height / span,
            price_offset = self.price_max * self.height / span,
        )

    def price_for_y(self, y: float) -> float:
        span = self.price_max - self.price_min
        return self.price_max - (y / self.height) * span

    def fit_to(self, candles: Sequence[Candle]) -> None:
        if not candles:
            return
        lo = min(c.low for c in candles)
        hi = max(c.high for c in candles)
        pad = max(hi - lo, 1e-9) * _FIT_PAD_FRAC
        self.price_min = lo - pad
        self.price_max = hi + pad

    def pan_by_px(self, px: float) -> None:
        if self.height <= 0.0:
            return
        span = self.price_max - self.price_min
        dprice = (px / self.height) * span
        self.price_min += dprice
        self.price_max += dprice

    def resize(self, height: float) -> None:
        self.height = height

    def range(self) -> tuple[float, float]:
        return (self.price_min, self.price_max)

    def labels(self, pane_inner_width: float) -> list[TextPrim]:
        ticks = price_ticks(self.price_min, self.price_max, self.height, _TARGET_TICKS)
        decimals = _decimals_for_span(self.price_max - self.price_min)
        return [
            TextPrim(
                text    = f"{t.price:.{decimals}f}",
                x       = pane_inner_width + _LABEL_INSET_PX,
                y       = t.y_px,
                size_px = _LABEL_SIZE,
                color   = _LABEL_COLOR,
            )
            for t in ticks
        ]


def _decimals_for_span(span: float) -> int:
    if   span >= 100.0: return 0
    elif span >= 10.0:  return 1
    elif span >= 1.0:   return 2
    elif span >= 0.1:   return 3
    else:               return 4
