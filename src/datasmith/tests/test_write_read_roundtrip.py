"""Cross-package contract test. Writes via datasmith, reads via core.
If row-group order or sentinel logic ever drifts between the two, this fails."""

from core.candle import ALL, Candle, Timeframe
from core.storage import read_candles
from datasmith.write import write_parquet


def _bars(start, count, step):
    return [
        Candle(ts=start + i * step, open=1, high=2, low=0.5, close=1.5, volume=i)
        for i in range(count)
    ]


def test_roundtrip_single_tf(tmp_path):
    h4 = _bars(0, 5, 14400)
    path = tmp_path / "ES.parquet"
    write_parquet(path, {Timeframe.H4: h4})

    got = read_candles(path, Timeframe.H4, 0, 2**63 - 1)
    assert got == h4


def test_roundtrip_multiple_tfs(tmp_path):
    bars = {
        Timeframe.M1: _bars(0, 60, 60),
        Timeframe.M15: _bars(0, 4, 900),
        Timeframe.D1: _bars(0, 1, 86400),
    }
    path = tmp_path / "X.parquet"
    write_parquet(path, bars)

    for tf, expected in bars.items():
        assert read_candles(path, tf, 0, 2**63 - 1) == expected


def test_roundtrip_empty_tfs_return_empty(tmp_path):
    path = tmp_path / "X.parquet"
    write_parquet(path, {Timeframe.D1: _bars(0, 2, 86400)})
    for tf in ALL:
        got = read_candles(path, tf, 0, 2**63 - 1)
        if tf is Timeframe.D1:
            assert len(got) == 2
        else:
            assert got == []   # sentinel rows filtered out


def test_roundtrip_all_empty_means_all_empty(tmp_path):
    path = tmp_path / "empty.parquet"
    write_parquet(path, {})
    for tf in ALL:
        assert read_candles(path, tf, 0, 2**63 - 1) == []
