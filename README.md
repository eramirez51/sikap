# sikap

Python port of [huge-td](../huge-td) — a backtesting/replay engine for OHLCV bars with a single-pane terminal UI.

## Layout

Three independent uv projects under `src/`:

- **`core`** — pure replay engine (no UI deps). `Candle`/`Timeframe` types, lazy parquet loader with `[range_from, range_to]` window, replay buffer with cursor, time/price axis projections, `AppEngine`/`EnginePane`.
- **`datasmith`** — Databento `.dbn.zst` → canonical parquet ingest CLI.
- **`tui`** — Textual + Pillow + Kitty graphics single-pane chart replay (`sikap-tui`).

`tui` and `datasmith` depend on `core` via editable path install. Each project has its own `.venv`.

## Quick start

```bash
make sync             # uv sync all three projects
make test             # run all pytest suites
```

### Ingest data

`datasmith` reads Databento `.dbn.zst` files in the magsi directory layout
(`<source>/<SYM>/ohlcv-1m/*.dbn.zst`) and writes a canonical parquet with one
row group per timeframe.

```bash
make data SYMBOL=ES DATA_SRC=/path/to/databento DATA_OUT=/tmp/sikap-data
```

`make data-all` ingests every subdirectory of `DATA_SRC`.

### Run the TUI

Requires a Kitty graphics-capable terminal (kitty, Ghostty, WezTerm).

```bash
make tui SYMBOL=ES DATA_OUT=/tmp/sikap-data TUI_TF=15m
```

Keys: `←/→` or `h/l` step, `f`/`r` fit, `q` quit.

## Cleanup

```bash
make clean        # remove pytest/build caches (keeps venvs)
make clean-venvs  # remove all .venv directories
```

## Design and plan

- Spec: `docs/superpowers/specs/2026-05-08-sikap-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-08-sikap-mvp.md`
