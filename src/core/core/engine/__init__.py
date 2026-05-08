"""AppEngine: top-level runtime tying Session + EnginePanes together.

Frontends consume `AppEngine`; internal helpers (Session, ReplayBuffer) are
not exposed beyond the engine module for v1.
"""

from __future__ import annotations

from pathlib import Path

from core.candle import Candle, Timeframe
from core.engine.events import (
    BarAppended,
    BarRemoved,
    BarUpdated,
    EngineEvent,
    NeedsRedraw,
    ViewportChanged,
)
from core.engine.pane import EnginePane
from core.session import Session


class AppEngine:
    def __init__(self, data_dir: Path) -> None:
        self._session = Session(data_dir)
        self._panes: list[EnginePane] = []
        self._next_id: int = 0

    def open(self, symbol: str, stepping_tf: Timeframe,
             range_from: int, range_to: int) -> None:
        self._session.open(symbol, stepping_tf, range_from, range_to)

    @property
    def session(self) -> Session:
        return self._session

    @property
    def panes(self) -> list[EnginePane]:
        return self._panes

    def pane(self, id: int) -> EnginePane | None:
        return next((p for p in self._panes if p.id == id), None)

    def pane_mut(self, id: int) -> EnginePane | None:
        # Python has no &mut/&; same accessor, conventional alias.
        return self.pane(id)

    def add_pane(self, symbol: str, tf: Timeframe,
                 width: float, height: float) -> int:
        pid = self._next_id
        self._next_id += 1
        candles = self._session.bars(symbol, tf)
        pane = EnginePane.new(pid, symbol, tf, width, height)
        pane.rebuild(candles)
        self._panes.append(pane)
        return pid

    def step(self, direction: int) -> list[EngineEvent]:
        before_lengths = [
            len(self._session.bars(p.symbol, p.tf))
            for p in self._panes
        ]
        self._session.step(direction)
        events: list[EngineEvent] = []
        for pane, before in zip(self._panes, before_lengths):
            candles = self._session.bars(pane.symbol, pane.tf)
            after = len(candles)
            pane.rebuild(candles)
            events.append(classify_step(before, after, pane.id))
        return events

    def seek(self, ts: int) -> EngineEvent:
        self._session.seek(ts)
        self.reload_all()
        return NeedsRedraw()

    def reload_all(self) -> None:
        for pane in self._panes:
            pane.rebuild(self._session.bars(pane.symbol, pane.tf))


def classify_step(before: int, after: int, pane_id: int) -> EngineEvent:
    """Pure: which EngineEvent describes a single pane's bar-count change?
    Append > Remove > Update; equal lengths mean the last bar's OHLC was
    overwritten."""
    if   after > before: return BarAppended(pane_id=pane_id)
    elif after < before: return BarRemoved(pane_id=pane_id)
    else:                return BarUpdated(pane_id=pane_id)
