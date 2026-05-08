"""Pure tick-generation math. No render dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PriceTick:
    """One labeled tick on the price axis."""
    price: float
    y_px: float


def price_ticks(
    price_min: float,
    price_max: float,
    height_px: float,
    target: int,
) -> list[PriceTick]:
    """Generate price ticks at "nice" round intervals (1, 2, 5 × 10^k) within
    [price_min, price_max], with roughly `target` ticks across `height_px`.

    `y_px` is measured top-down: 0 at price_max, height_px at price_min.
    """
    span = price_max - price_min
    if span <= 0.0 or height_px <= 0.0 or target == 0:
        return []

    step = nice_step(span / target)
    first = math.ceil(price_min / step) * step

    out: list[PriceTick] = []
    p = first
    while p <= price_max:
        frac = (price_max - p) / span
        out.append(PriceTick(price=p, y_px=frac * height_px))
        p += step
    return out


def nice_step(raw: float) -> float:
    """Round `raw` up to the nearest 1, 2, or 5 × 10^k."""
    if raw <= 0.0:
        return 1.0
    exp = math.floor(math.log10(raw))
    pow10 = 10.0 ** exp
    frac = raw / pow10
    if frac <= 1.0:
        nice = 1.0
    elif frac <= 2.0:
        nice = 2.0
    elif frac <= 5.0:
        nice = 5.0
    else:
        nice = 10.0
    return nice * pow10
