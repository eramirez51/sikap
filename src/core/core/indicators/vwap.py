"""Anchored VWAP with volume-weighted standard-deviation bands.

The hot loop runs in the `native` Rust crate (see
`native::compute_vwap`). Python is a thin shim that:

1. Materializes candles into `array.array('d')` / `('q')` views (read by
   Rust via the buffer protocol — zero copy on the borrowed side).
2. Maps the public `anchor` callable to the matching `native.Anchor`
   enum variant; if it isn't one of the three built-in helpers, falls
   back to a pure-Python loop so user-defined anchors still work.
3. Wraps the flat output arrays into a `VwapResult` (struct-of-arrays).

Typical price is HLC3 = (high + low + close) / 3.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Sequence
from zoneinfo import ZoneInfo

import native

from core.candle import Candle
from core.chart.primitives import Overlay, Polyline, Rgba


_ET = ZoneInfo("America/New_York")


def globex_daily_anchor(ts: int) -> int:
    """Most recent 18:00 ET (CME Globex daily session open) at or before ts."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_ET)
    base = dt.replace(hour=18, minute=0, second=0, microsecond=0)
    if dt.hour < 18:
        base -= timedelta(days=1)
    return int(base.astimezone(timezone.utc).timestamp())


def rth_us_anchor(ts: int) -> int:
    """Most recent 09:30 ET (US regular-trading-hours open) at or before ts."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_ET)
    base = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    if dt.hour < 9 or (dt.hour == 9 and dt.minute < 30):
        base -= timedelta(days=1)
    return int(base.astimezone(timezone.utc).timestamp())


def utc_daily_anchor(ts: int) -> int:
    """Most recent 00:00 UTC at or before ts."""
    return ts - (ts % 86400)


# Identity-map the three built-in anchors to native enum variants. Keeps
# the public API a Callable while routing the common path to Rust.
_ANCHOR_TO_NATIVE: dict[Callable[[int], int], "native.Anchor"] = {
    globex_daily_anchor: native.Anchor.GlobexDaily,
    rth_us_anchor:       native.Anchor.RthUs,
    utc_daily_anchor:    native.Anchor.UtcDaily,
}


@dataclass(frozen=True, slots=True)
class VwapResult:
    """Struct-of-arrays VWAP output. All arrays are length n.

    `lower[k][i]` and `upper[k][i]` give the (lower, upper) band edges
    for `k_bands[k]` at bar index i.

    `session_starts` is the list of bar indices where a new anchor
    session begins (always starts with 0). The overlay builder uses it
    to split the line into per-session polylines so the renderer never
    draws a segment across a session reset.
    """
    n:              int
    k_bands:        tuple[float, ...]
    ts:             array              # 'q' (i64)
    vwap:           array              # 'd' (f64)
    std:            array              # 'd' (f64)
    lower:          tuple[array, ...]  # one 'd' array per k
    upper:          tuple[array, ...]
    session_starts: array              # 'q' (i64)


def compute_vwap(
    candles: Sequence[Candle],
    *,
    anchor: Callable[[int], int] = globex_daily_anchor,
    k_bands: tuple[float, ...] = (1.0, 1.75, 2.0),
) -> VwapResult:
    """Anchored VWAP + volume-weighted SD bands."""
    n = len(candles)
    n_bands = len(k_bands)

    if n == 0:
        return VwapResult(
            n=0, k_bands=tuple(k_bands),
            ts=array("q"), vwap=array("d"), std=array("d"),
            lower=tuple(array("d") for _ in range(n_bands)),
            upper=tuple(array("d") for _ in range(n_bands)),
            session_starts=array("q"),
        )

    native_anchor = _ANCHOR_TO_NATIVE.get(anchor)
    if native_anchor is not None:
        return _compute_native(candles, native_anchor, k_bands)
    return _compute_python(candles, anchor, k_bands)


def _compute_native(
    candles: Sequence[Candle],
    native_anchor: "native.Anchor",
    k_bands: tuple[float, ...],
) -> VwapResult:
    n       = len(candles)
    n_bands = len(k_bands)
    ts_in    = array("q", (c.ts     for c in candles))
    opens_in = array("d", (c.open   for c in candles))
    highs_in = array("d", (c.high   for c in candles))
    lows_in  = array("d", (c.low    for c in candles))
    closes_in= array("d", (c.close  for c in candles))
    vols_in  = array("q", (c.volume for c in candles))

    rust_ts, rust_vwap, rust_std, rust_lower, rust_upper, rust_session_starts = (
        native.compute_vwap(
            ts_in, opens_in, highs_in, lows_in, closes_in, vols_in,
            native_anchor, list(k_bands),
        )
    )
    # Rust returns Python lists; copy once into typed arrays so the
    # rasterizer can hand them to Rust via the buffer protocol later.
    lower = tuple(
        array("d", rust_lower[k * n : (k + 1) * n])
        for k in range(n_bands)
    )
    upper = tuple(
        array("d", rust_upper[k * n : (k + 1) * n])
        for k in range(n_bands)
    )
    return VwapResult(
        n=n,
        k_bands=tuple(k_bands),
        ts=array("q", rust_ts),
        vwap=array("d", rust_vwap),
        std=array("d", rust_std),
        lower=lower,
        upper=upper,
        session_starts=array("q", rust_session_starts),
    )


def _compute_python(
    candles: Sequence[Candle],
    anchor: Callable[[int], int],
    k_bands: tuple[float, ...],
) -> VwapResult:
    """Pure-Python fallback for custom anchors. Not used by the built-in
    Globex/RTH/UTC helpers — those take the Rust path."""
    n = len(candles)
    n_bands = len(k_bands)
    ts_out   = array("q", [0] * n)
    vwap_out = array("d", [0.0] * n)
    std_out  = array("d", [0.0] * n)
    lower    = tuple(array("d", [0.0] * n) for _ in range(n_bands))
    upper    = tuple(array("d", [0.0] * n) for _ in range(n_bands))

    session_starts: list[int] = []
    cur_anchor: int | None = None
    sum_v = sum_pv = sum_ppv = 0.0
    for i, c in enumerate(candles):
        a = anchor(c.ts)
        if a != cur_anchor:
            cur_anchor = a
            sum_v = sum_pv = sum_ppv = 0.0
            session_starts.append(i)
        tp = (c.high + c.low + c.close) / 3.0
        if c.volume > 0:
            v = float(c.volume)
            sum_v   += v
            sum_pv  += tp * v
            sum_ppv += tp * tp * v
        if sum_v > 0.0:
            vwap = sum_pv / sum_v
            var  = sum_ppv / sum_v - vwap * vwap
            std  = sqrt(var) if var > 0.0 else 0.0
        else:
            vwap = tp
            std  = 0.0
        ts_out[i]   = c.ts
        vwap_out[i] = vwap
        std_out[i]  = std
        for k_idx, k in enumerate(k_bands):
            lower[k_idx][i] = vwap - k * std
            upper[k_idx][i] = vwap + k * std
    return VwapResult(
        n=n, k_bands=tuple(k_bands),
        ts=ts_out, vwap=vwap_out, std=std_out,
        lower=lower, upper=upper,
        session_starts=array("q", session_starts),
    )


# Default colors. Center line gold; bands fade out as k grows.
_VWAP_CENTER:    Rgba = (1.00, 0.85, 0.10, 1.00)
_VWAP_BAND_1_0:  Rgba = (1.00, 0.85, 0.10, 0.55)
_VWAP_BAND_1_75: Rgba = (1.00, 0.85, 0.10, 0.40)
_VWAP_BAND_2_0:  Rgba = (1.00, 0.85, 0.10, 0.28)


def vwap_overlay(
    result: VwapResult,
    *,
    name: str = "vwap",
    center_color: Rgba = _VWAP_CENTER,
    band_colors: tuple[Rgba, ...] = (_VWAP_BAND_1_0, _VWAP_BAND_1_75, _VWAP_BAND_2_0),
    width: float = 1.0,
) -> Overlay:
    """Build a renderable Overlay from a VwapResult.

    Emits one center-line polyline plus two polylines per band (upper +
    lower). The xs axis is `[0, 1, 2, …, n-1]` — bar index, aligned
    with `pane.candles` whose result was computed.

    `band_colors[i]` styles `result.k_bands[i]`. If more bands exist
    than `band_colors` provides, the extras reuse the last color.
    """
    if result.n == 0:
        return Overlay(name=name, polylines=())

    # One polyline per band line, spanning the whole result. The renderer
    # connects every adjacent pair of points, including across session
    # boundaries — at a reset the band naturally "pinches" toward the
    # first bar's typical price (std=0) and fans back out as the new
    # session accumulates volume. Matches TV's continuous-line look.
    xs = array("d", range(result.n))
    polylines: list[Polyline] = [
        Polyline(xs=xs, ys=result.vwap, color=center_color, width=width),
    ]
    last_color = band_colors[-1] if band_colors else center_color
    for k_idx in range(len(result.k_bands)):
        color = band_colors[k_idx] if k_idx < len(band_colors) else last_color
        polylines.append(Polyline(xs=xs, ys=result.upper[k_idx], color=color, width=width))
        polylines.append(Polyline(xs=xs, ys=result.lower[k_idx], color=color, width=width))

    return Overlay(name=name, polylines=tuple(polylines))
