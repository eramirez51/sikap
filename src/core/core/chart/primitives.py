"""Chart-space drawing primitives that indicators emit.

Anchored in (bar_index, price). The rasterizer applies the active
`Viewport` to project to pixels:
    cx = bar_index * bar_spacing + x_offset
    cy = price     * price_scale + price_offset

Pixel-space primitives (axis labels) live in `core.chart.scene`.

Coordinates are kept in `array.array('d', …)` rather than tuples so a
polyline of N points is one allocation, not N. The rasterizer hands
these arrays to the Rust native crate via the buffer protocol — one
zero-copy borrow per axis, no per-point object overhead.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass


Rgba = tuple[float, float, float, float]   # 0.0..1.0 each


@dataclass(frozen=True, slots=True)
class Polyline:
    """An anti-aliased polyline in chart-space (bar_index, price).

    `xs` and `ys` are parallel `array.array('d', …)` buffers; their
    lengths must match. A polyline of fewer than two points draws nothing.
    """
    xs:    array
    ys:    array
    color: Rgba
    width: float = 1.0


@dataclass(frozen=True, slots=True)
class Overlay:
    """A named bundle of primitives produced by one indicator.

    The `name` lets the host attach/replace/remove the bundle by
    identity (e.g. recompute VWAP and replace the prior overlay).
    """
    name: str
    polylines: tuple[Polyline, ...] = ()
