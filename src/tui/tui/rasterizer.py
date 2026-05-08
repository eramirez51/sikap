"""Rust-backed rasterizer.

The whole render path lives in the `native` crate now: background fill,
candle bodies + wicks, axis labels (via fontdue with the embedded
JetBrainsMono TTF). Python's only job is to marshal data into the four
parallel buffers that Rust reads zero-copy.

Both halves still consume the engine's `XProjection` / `YProjection` —
same single source of truth as the rest of the pipeline.
"""

from __future__ import annotations

import array

import native

from core.engine.pane import EnginePane


_BG_COLOR        = (20, 20, 28, 255)
_BULL_COLOR      = (38, 166, 154, 255)
_BEAR_COLOR      = (239, 83, 80, 255)
_GUTTER_SEP      = (60, 60, 70, 255)   # 1px line between candle area + gutter
_BODY_FRAC       = 0.8                  # body width = 0.8 × bar_spacing


def _to_u8_rgba(c: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return (int(c[0] * 255), int(c[1] * 255), int(c[2] * 255), int(c[3] * 255))


class Rasterizer:
    def __init__(self, width: int, height: int) -> None:
        self.width  = max(1, width)
        self.height = max(1, height)
        self._native = native.Rasterizer(self.width, self.height)

    def resize(self, width: int, height: int) -> None:
        width  = max(1, width)
        height = max(1, height)
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self._native.resize(width, height)

    def render(self, pane: EnginePane) -> bytes:
        # Parallel float arrays — array.array('d', …) exposes the buffer
        # protocol so PyO3 reads them zero-copy as &[f64] on the Rust side.
        opens  = array.array("d", (c.open  for c in pane.candles))
        highs  = array.array("d", (c.high  for c in pane.candles))
        lows   = array.array("d", (c.low   for c in pane.candles))
        closes = array.array("d", (c.close for c in pane.candles))

        # Pane axes describe the candle area, not the full buffer. Labels
        # are positioned at `pane_inner_{width,height} + inset`, so passing
        # the pane's own dimensions lands them inside the gutters of the
        # rasterizer buffer (where there are no candles).
        labels = [
            (tp.text, tp.x, tp.y, tp.size_px, _to_u8_rgba(tp.color))
            for tp in pane.price.labels(pane.time.width)
        ] + [
            (tp.text, tp.x, tp.y, tp.size_px, _to_u8_rgba(tp.color))
            for tp in pane.time.labels(pane.candles, pane.price.height)
        ]

        return self._native.render(
            bg         = _BG_COLOR,
            bull       = _BULL_COLOR,
            bear       = _BEAR_COLOR,
            gutter_sep = _GUTTER_SEP,
            body_frac  = _BODY_FRAC,
            bar_width  = pane.time.bar_width,
            viewport   = pane.viewport(),
            chart_w    = int(pane.time.width),
            chart_h    = int(pane.price.height),
            opens      = opens,
            highs      = highs,
            lows       = lows,
            closes     = closes,
            labels     = labels,
        )
