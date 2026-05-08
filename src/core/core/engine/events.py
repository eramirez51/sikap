"""EngineEvent: a tagged-union of state-change events the engine emits.

Per-variant frozen dataclasses + a PEP 604 union type. `match` works:

    for ev in engine.step(1):
        match ev:
            case BarAppended(pane_id=pid):  ...
            case BarUpdated(pane_id=pid):   ...
            case ...
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NeedsRedraw:
    """Full re-render needed (seek, structural change)."""


@dataclass(frozen=True, slots=True)
class BarAppended:
    """A new bar finished and was appended to this pane's TF buffer."""
    pane_id: int


@dataclass(frozen=True, slots=True)
class BarRemoved:
    """The last bar was removed (backward step)."""
    pane_id: int


@dataclass(frozen=True, slots=True)
class BarUpdated:
    """The last (forming) bar's OHLC changed but no new bar was added."""
    pane_id: int


@dataclass(frozen=True, slots=True)
class ViewportChanged:
    """Pan/zoom/resize: viewport changed, mesh vertices unchanged."""
    pane_id: int


EngineEvent = NeedsRedraw | BarAppended | BarRemoved | BarUpdated | ViewportChanged
