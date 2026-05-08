"""datasmith CLI.

Usage:
    datasmith ingest databento SYM --source <dir> --out <dir>
    datasmith ingest databento --all --source <dir> --out <dir>

Source directory layout (matches magsi):
    <source>/<SYM>/ohlcv-1m/*.dbn.zst
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.candle import Timeframe

from datasmith.aggregate import aggregate
from datasmith.databento import read_m1_bars
from datasmith.write import write_parquet


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datasmith")
    sub_top = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub_top.add_parser("ingest", help="ingest market data into canonical parquet")
    # `dest="backend"` to avoid collision with --source flag below.
    sub_ingest = p_ingest.add_subparsers(dest="backend", required=True)

    p_db = sub_ingest.add_parser("databento", help="ingest .dbn.zst files")
    p_db.add_argument("symbol", nargs="?", help="symbol name (omit when using --all)")
    p_db.add_argument("--source", type=Path, required=True,
                      help="source root containing <SYM>/ohlcv-1m/")
    p_db.add_argument("--out",    type=Path, required=True,
                      help="output directory for {SYM}.parquet")
    p_db.add_argument("--all", action="store_true",
                      help="ingest every subdirectory of --source")

    return parser


def _ingest_one(symbol: str, source_root: Path, out_root: Path) -> None:
    dbn_dir = source_root / symbol / "ohlcv-1m"
    print(f"  reading {dbn_dir}")
    m1 = read_m1_bars(dbn_dir, progress=True)
    print(f"  M1: {len(m1)} bars")

    by_tf: dict[Timeframe, list] = {Timeframe.M1: m1}
    for tf in Timeframe:
        if tf is Timeframe.M1:
            continue
        agg = aggregate(m1, tf)
        if agg:
            by_tf[tf] = agg
            print(f"  {tf}: {len(agg)} bars")

    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{symbol}.parquet"
    write_parquet(out_path, by_tf, progress=True)

    size_mb = out_path.stat().st_size / 1_048_576
    print(f"  wrote {out_path} ({size_mb:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command != "ingest" or args.backend != "databento":
        print(f"unknown command/backend combo", file=sys.stderr)
        return 2

    if args.all:
        for entry in sorted(args.source.iterdir()):
            if entry.is_dir():
                _ingest_one(entry.name, args.source, args.out)
    else:
        if not args.symbol:
            print("error: provide a symbol or use --all", file=sys.stderr)
            return 2
        _ingest_one(args.symbol, args.source, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
