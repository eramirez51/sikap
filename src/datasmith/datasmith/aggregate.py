"""Bucket finer-resolution candles into coarser timeframes."""

from __future__ import annotations

from typing import Sequence

from core.candle import Candle, Timeframe


def aggregate(src: Sequence[Candle], tf: Timeframe) -> list[Candle]:
    """Bucket finer-resolution bars into `tf` buckets. Caller must pre-sort
    `src` by ts ascending."""
    tf_secs = tf.seconds
    out: list[Candle] = []
    for bar in src:
        if tf is Timeframe.W1:
            # weekly: align to Monday (Unix epoch was a Thursday, offset 4 days)
            bucket_ts = bar.ts - ((bar.ts - 345_600) % 604_800)
        else:
            bucket_ts = (bar.ts // tf_secs) * tf_secs

        if out and out[-1].ts == bucket_ts:
            last = out[-1]
            out[-1] = Candle(
                ts     = last.ts,
                open   = last.open,
                high   = max(last.high, bar.high),
                low    = min(last.low,  bar.low),
                close  = bar.close,
                volume = last.volume + bar.volume,
            )
        else:
            out.append(Candle(
                ts     = bucket_ts,
                open   = bar.open,
                high   = bar.high,
                low    = bar.low,
                close  = bar.close,
                volume = bar.volume,
            ))
    return out
