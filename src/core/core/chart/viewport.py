"""Viewport — the chart's bar↔pixel and price↔pixel transform as one value.

A `Viewport` packages the four scalars `(bar_spacing, x_offset,
price_scale, price_offset)` that describe the affine mapping from
(bar_index, price) to (x, y) pixels. It's the single source of truth
shared between the engine (which computes it) and the rasterizer
(which consumes it). The Rust rasterizer reads the same struct via
PyO3's buffer-protocol-equivalent attribute extraction, so the chart's
geometry crosses the FFI as one named bundle instead of five
positional kwargs.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.chart.price_axis import YProjection
from core.chart.time_axis  import XProjection


@dataclass(frozen=True, slots=True)
class Viewport:
    """The four scalars of the chart's affine pixel mapping.

    Vertex math (matches the Rust shader convention):
        cx = bar_index * bar_spacing + x_offset
        cy = price     * price_scale + price_offset
    """
    bar_spacing:  float
    x_offset:     float
    price_scale:  float
    price_offset: float

    @classmethod
    def from_projections(cls, x: XProjection, y: YProjection) -> "Viewport":
        return cls(
            bar_spacing  = x.bar_spacing,
            x_offset     = x.x_offset,
            price_scale  = y.price_scale,
            price_offset = y.price_offset,
        )
