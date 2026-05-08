from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int        # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: int


class Timeframe(Enum):
    """Value is the timeframe in minutes; .seconds returns value * 60.

    The canonical row-group order is `core.candle.ALL` — both the parquet
    writer (datasmith) and reader (core.storage) read that tuple, so the order
    cannot drift if every code path imports `ALL` rather than hardcoding.
    """

    M1 = 1
    M2 = 2
    M3 = 3
    M4 = 4
    M5 = 5
    M6 = 6
    M7 = 7
    M8 = 8
    M9 = 9
    M10 = 10
    M15 = 15
    H1 = 60
    H2 = 120
    H3 = 180
    H4 = 240
    D1 = 1440
    W1 = 10080

    @property
    def seconds(self) -> int:
        return self.value * 60

    def __str__(self) -> str:
        return _TF_STRINGS[self]

    @classmethod
    def from_str(cls, s: str) -> "Timeframe":
        try:
            return _TF_FROM_STR[s]
        except KeyError as exc:
            raise ValueError(f"unknown timeframe: {s!r}") from exc


_TF_STRINGS: dict[Timeframe, str] = {
    Timeframe.M1:  "1m",
    Timeframe.M2:  "2m",
    Timeframe.M3:  "3m",
    Timeframe.M4:  "4m",
    Timeframe.M5:  "5m",
    Timeframe.M6:  "6m",
    Timeframe.M7:  "7m",
    Timeframe.M8:  "8m",
    Timeframe.M9:  "9m",
    Timeframe.M10: "10m",
    Timeframe.M15: "15m",
    Timeframe.H1:  "1h",
    Timeframe.H2:  "2h",
    Timeframe.H3:  "3h",
    Timeframe.H4:  "4h",
    Timeframe.D1:  "1d",
    Timeframe.W1:  "1w",
}

_TF_FROM_STR: dict[str, Timeframe] = {v: k for k, v in _TF_STRINGS.items()}


# Canonical row-group order shared by datasmith.write and core.storage.
# Position in this tuple IS the row-group index for that TF in the parquet
# file. Both sides MUST iterate this; never hardcode order independently.
ALL: tuple[Timeframe, ...] = (
    Timeframe.M1,  Timeframe.M2,  Timeframe.M3,  Timeframe.M4,
    Timeframe.M5,  Timeframe.M6,  Timeframe.M7,  Timeframe.M8,
    Timeframe.M9,  Timeframe.M10, Timeframe.M15,
    Timeframe.H1,  Timeframe.H2,  Timeframe.H3,  Timeframe.H4,
    Timeframe.D1,  Timeframe.W1,
)


def row_group_index(tf: Timeframe) -> int:
    """Position of `tf` in `ALL`. Programmer error if missing."""
    return ALL.index(tf)
