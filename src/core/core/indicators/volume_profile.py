"""Volume profile + High Volume Node detection.

Volume profile is a histogram of transacted volume by price bin over a
window of bars. A High Volume Node (HVN) is a bin whose total volume is
significantly above the window's average — the algorithms.md strategy
treats price entering an HVN as one of the entry conditions.

Pure OHLCV inputs; no aggressor-side flow needed (CVD is a separate
indicator).

Distribution model: each bar's volume is split uniformly across every
bin its `[low, high]` range touches. This attributes activity to every
price the bar visited, so a wick that pokes into a high-resistance
level builds the HVN there even if the bar closed elsewhere. Matches
what the algorithms.md reference visualizes — bands at price levels
the market *interacted* with, not just where bars happened to close.
"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass

from core.candle import Candle
from core.chart.primitives import Overlay, Rect, Rgba


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """Histogram of volume by price bin over a window of bars.

    `bin_volumes[i]` is the total volume in `[bin_low(i), bin_low(i+1))`,
    where `bin_low(i) = price_min + i * bin_size`. `n_bins` bins span
    `[price_min, price_max]` exactly.
    """
    n_bins:       int
    price_min:    float
    price_max:    float
    bin_volumes:  array       # 'd' (f64); length n_bins
    window_first: int         # bar index of the first bar in the window
    window_last:  int         # bar index of the last  bar in the window

    @property
    def bin_size(self) -> float:
        return (self.price_max - self.price_min) / self.n_bins if self.n_bins else 0.0


def compute_volume_profile(
    candles: Sequence[Candle],
    *,
    window:       int = 60,
    n_bins:       int = 50,
    end_index:    int | None = None,
) -> VolumeProfile | None:
    """Compute the volume profile for the `window` bars ending at
    `end_index` (default: last bar).

    Returns `None` when there isn't enough data, or when the window's
    price range is degenerate (max == min).
    """
    n = len(candles)
    if n == 0 or window <= 0 or n_bins <= 0:
        return None
    last  = n - 1 if end_index is None else min(end_index, n - 1)
    first = max(0, last - window + 1)
    if last < first:
        return None
    win = candles[first : last + 1]

    price_min = min(c.low  for c in win)
    price_max = max(c.high for c in win)
    if price_max <= price_min:
        return None
    bin_size = (price_max - price_min) / n_bins

    bin_volumes = array("d", [0.0] * n_bins)
    for c in win:
        if c.volume <= 0:
            continue
        # First and last bin the bar's range touches. Clamp to [0, n_bins-1].
        i_lo = int((c.low  - price_min) / bin_size)
        i_hi = int((c.high - price_min) / bin_size)
        if i_lo < 0:           i_lo = 0
        if i_hi >= n_bins:     i_hi = n_bins - 1
        if i_hi < i_lo:
            continue
        share = c.volume / (i_hi - i_lo + 1)
        for i in range(i_lo, i_hi + 1):
            bin_volumes[i] += share

    return VolumeProfile(
        n_bins=n_bins,
        price_min=price_min,
        price_max=price_max,
        bin_volumes=bin_volumes,
        window_first=first,
        window_last=last,
    )


def hvn_ranges(
    profile: VolumeProfile,
    *,
    threshold: float = 1.6,
) -> list[tuple[float, float]]:
    """Identify High Volume Nodes and merge contiguous bins into price
    ranges.

    A bin qualifies when `bin_volume >= threshold * mean(bin_volumes)`.
    Returns merged `(price_low, price_high)` pairs; empty list when no
    bin clears the threshold.
    """
    n = profile.n_bins
    if n == 0:
        return []
    total = sum(profile.bin_volumes)
    if total <= 0:
        return []
    mean = total / n
    cutoff = threshold * mean
    bin_size = profile.bin_size

    ranges: list[tuple[float, float]] = []
    in_run = False
    run_start = 0
    for i in range(n):
        if profile.bin_volumes[i] >= cutoff:
            if not in_run:
                in_run = True
                run_start = i
        else:
            if in_run:
                lo = profile.price_min + run_start * bin_size
                hi = profile.price_min + i         * bin_size
                ranges.append((lo, hi))
                in_run = False
    if in_run:
        lo = profile.price_min + run_start * bin_size
        hi = profile.price_min + n         * bin_size
        ranges.append((lo, hi))
    return ranges


# Default tint: cyan-ish, low alpha so the bands sit behind candles.
_HVN_COLOR: Rgba = (0.30, 0.65, 1.00, 0.14)


def hvn_overlay(
    profile: VolumeProfile | None,
    *,
    name:       str   = "hvn",
    threshold:  float = 1.6,
    color:      Rgba  = _HVN_COLOR,
) -> Overlay:
    """Build a renderable Overlay of HVN bands for `profile`.

    Each HVN price range becomes one filled `Rect` spanning the window
    the profile was computed over (`window_first..window_last` on the
    bar-index axis).
    """
    if profile is None:
        return Overlay(name=name)
    rects = tuple(
        Rect(
            x1=float(profile.window_first),
            y1=lo,
            x2=float(profile.window_last),
            y2=hi,
            color=color,
        )
        for lo, hi in hvn_ranges(profile, threshold=threshold)
    )
    return Overlay(name=name, rects=rects)
