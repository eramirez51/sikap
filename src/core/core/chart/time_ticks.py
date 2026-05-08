"""Pure time-tick math. No render dependencies."""

from __future__ import annotations

# Candidate intervals in seconds, sorted ascending.
# 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 2d, 1w, ~1mo.
STEPS_SECS: tuple[int, ...] = (
    60, 300, 900, 1800, 3600, 7200, 14400, 21600, 43200,
    86400, 172800, 604800, 2_592_000,
)


def nice_step(raw: int) -> int:
    """Smallest STEPS_SECS value ≥ `raw`. Floors below min → first;
    above max → last."""
    if raw <= STEPS_SECS[0]:
        return STEPS_SECS[0]
    for s in STEPS_SECS:
        if s >= raw:
            return s
    return STEPS_SECS[-1]
