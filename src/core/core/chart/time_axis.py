"""The x-axis as one module: bar↔pixel mapping, viewport state, and the
labels rendered into the time gutter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from core.candle import Candle
from core.chart.scene import TextPrim
from core.chart.time_ticks import nice_step


_TARGET_TICKS:   int                                = 7
_LABEL_INSET_PX: float                              = 4.0
_LABEL_SIZE:     float                              = 11.0
_LABEL_COLOR:    tuple[float, float, float, float]   = (0.78, 0.78, 0.82, 1.0)


@dataclass(frozen=True, slots=True)
class XProjection:
    """Linear bar-index→pixel-x projection. Shared with the rasterizer."""
    bar_spacing: float
    x_offset:    float


@dataclass
class TimeAxis:
    bar_width:  float    # pixels per bar (zoom)
    base_index: float    # index of rightmost visible bar (fractional → smooth pan)
    width:      float    # pane width in pixels

    def x_for_bar(self, index: int) -> float:
        p = self.projection()
        return index * p.bar_spacing + p.x_offset

    def projection(self) -> XProjection:
        return XProjection(
            bar_spacing = self.bar_width,
            x_offset    = self.width
                          - self.bar_width / 2.0
                          - self.base_index * self.bar_width,
        )

    def bar_for_x(self, x: float) -> int:
        from_right = int((self.width - x) // self.bar_width)
        return max(0, int(self.base_index) - from_right)

    def visible_range(self) -> tuple[int, int]:
        import math
        visible = math.ceil(self.width / self.bar_width) if self.bar_width > 0 else 1
        # Ceil so bars partially visible at the right edge (when base_index is
        # fractional) are included.
        last  = math.ceil(self.base_index)
        first = max(0, last - max(visible - 1, 0))
        return (first, last)

    def zoom_bar_width(self, factor: float) -> None:
        """Multiplicative zoom on bar width. `factor>1` widens bars (zoom in);
        `factor<1` narrows them (zoom out). Clamped to [2, 64]."""
        if factor <= 0.0:
            return
        self.bar_width = max(2.0, min(64.0, self.bar_width * factor))

    def resize(self, width: float) -> None:
        self.width = width

    def labels(self, candles: Sequence[Candle], pane_inner_height: float) -> list[TextPrim]:
        if not candles:
            return []
        last_idx = len(candles) - 1
        first, last = self.visible_range()
        first = min(first, last_idx)
        last  = min(last,  last_idx)
        if first >= last:
            return []

        span_secs = candles[last].ts - candles[first].ts
        if span_secs <= 0:
            return []

        step = nice_step(span_secs // _TARGET_TICKS)
        label_y = pane_inner_height + _LABEL_INSET_PX

        # Bucket of the bar just before `first` so the first qualifying
        # bar in range emits a tick.
        if first == 0:
            prev_bucket = candles[first].ts // step - 1
        else:
            prev_bucket = candles[first - 1].ts // step

        out: list[TextPrim] = []
        for i in range(first, last + 1):
            bucket = candles[i].ts // step
            if bucket > prev_bucket:
                prev_bucket = bucket
                out.append(TextPrim(
                    text    = _format_tick(candles[i].ts, step),
                    x       = self.x_for_bar(i),
                    y       = label_y,
                    size_px = _LABEL_SIZE,
                    color   = _LABEL_COLOR,
                ))
        return out


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _format_tick(ts: int, step_secs: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if step_secs >= 86400 or (dt.hour == 0 and dt.minute == 0):
        return f"{_MONTHS[dt.month - 1]} {dt.day}"
    return f"{dt.hour:02d}:{dt.minute:02d}"
