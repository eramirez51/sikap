# sikap — Python port of huge-td core/ingest/TUI

**Date:** 2026-05-08
**Status:** design approved, awaiting plan

## 1. Goal & scope

Build a Python equivalent of three layers from the Rust [`huge-td`](../../../../../huge-td) project:

- **`datasmith`** — ingest CLI that decodes Databento `.dbn.zst` files, aggregates M1 bars up into all higher timeframes, and writes a canonical parquet file (one row group per timeframe).
- **`core`** — pure replay engine: candle types, lazy parquet loader, replay buffer with timestamp cursor, range window, and pure axis math (time/price projections, ticks). No UI dependencies.
- **`tui`** — terminal frontend rendering a single chart pane via the Kitty graphics protocol. Built on Textual for layout/widgets and Pillow for rasterizing candles into the RGBA buffer that gets transmitted to the terminal.

**MVP boundaries (explicitly out of scope for v1):**

- No Lua config (`init.lua` keymap/layout/palette).
- No multi-pane split layout, divider drag, or pane navigation.
- No drawing tools (HRay, Trendline, Rectangle, Position).
- No trade panel, trades, or trade statistics.
- No pattern detectors (FVG, swing, order block, etc.).
- No animator (the 2-frame TradingView-style step animation).
- No debug HTTP server.
- No Yahoo or other ingest sources beyond Databento.

These are recorded so deferred features stay visible; they may land later in their own specs.

## 2. Repo layout

Three independent uv projects (each with its own `.venv`), all under `src/`:

```
sikap/
├── docs/superpowers/specs/2026-05-08-sikap-design.md
├── src/
│   ├── core/
│   │   ├── pyproject.toml          # name = "core"
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── candle.py
│   │   │   ├── storage.py
│   │   │   ├── replay.py
│   │   │   ├── session.py
│   │   │   ├── engine/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── pane.py
│   │   │   │   └── events.py
│   │   │   └── chart/
│   │   │       ├── __init__.py
│   │   │       ├── ticks.py
│   │   │       ├── time_ticks.py
│   │   │       ├── price_axis.py
│   │   │       ├── time_axis.py
│   │   │       └── scene.py
│   │   └── tests/
│   ├── datasmith/
│   │   ├── pyproject.toml          # name = "datasmith"
│   │   ├── datasmith/
│   │   │   ├── __init__.py
│   │   │   ├── __main__.py
│   │   │   ├── cli.py
│   │   │   ├── databento.py
│   │   │   ├── aggregate.py
│   │   │   └── write.py
│   │   └── tests/
│   └── tui/
│       ├── pyproject.toml          # name = "tui"
│       ├── tui/
│       │   ├── __init__.py
│       │   ├── __main__.py
│       │   ├── app.py
│       │   ├── chart_widget.py
│       │   ├── rasterizer.py
│       │   ├── kitty.py
│       │   ├── input.py
│       │   └── fonts/JetBrainsMono-Regular.ttf
│       └── tests/
└── README.md
```

**Dependency direction:** `core` is leaf. `datasmith` and `tui` each declare `core` as an editable path dependency:

```toml
# datasmith/pyproject.toml and tui/pyproject.toml
[tool.uv.sources]
core = { path = "../core", editable = true }
```

Editable means changes to `core/core/*.py` are reflected immediately in both consumer venvs without rebuilding. Each project has its own `.venv` (per the user's preference for independent projects).

**Build backend:** hatchling (uv's default). Auto-discovers the inner same-named package directory; no extra config needed.

**CLI entry points:**

```toml
# datasmith/pyproject.toml
[project.scripts]
datasmith = "datasmith.cli:main"

# tui/pyproject.toml
[project.scripts]
sikap-tui = "tui.app:main"
```

The `tui` package's binary is named `sikap-tui` (a literal `tui` would be too generic on PATH).

## 3. `core` API

Direct port of `huge-td/src/core/src/`. Each Rust module maps to one Python module of the same name.

### 3.1 `candle.py`

```python
@dataclass(frozen=True, slots=True)
class Candle:
    ts: int        # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: int

class Timeframe(Enum):
    M1 = 1; M2 = 2; M3 = 3; M4 = 4; M5 = 5; M6 = 6; M7 = 7; M8 = 8; M9 = 9
    M10 = 10; M15 = 15
    H1 = 60; H2 = 120; H3 = 180; H4 = 240
    D1 = 1440; W1 = 10080
    # value = minutes; .seconds returns value * 60

    @property
    def seconds(self) -> int: ...
    def __str__(self) -> str: ...      # "1m", "15m", "1h", "1d"
    @classmethod
    def from_str(cls, s: str) -> "Timeframe": ...

ALL: tuple[Timeframe, ...] = (
    Timeframe.M1, Timeframe.M2, Timeframe.M3, Timeframe.M4,
    Timeframe.M5, Timeframe.M6, Timeframe.M7, Timeframe.M8,
    Timeframe.M9, Timeframe.M10, Timeframe.M15,
    Timeframe.H1, Timeframe.H2, Timeframe.H3, Timeframe.H4,
    Timeframe.D1, Timeframe.W1,
)

def row_group_index(tf: Timeframe) -> int:
    return ALL.index(tf)
```

`ALL` is the contract shared with `datasmith.write` — both sides import the same tuple, so row-group order cannot drift.

### 3.2 `storage.py`

```python
def read_candles(path: Path, tf: Timeframe, from_ts: int, to_ts: int) -> list[Candle]:
    """Read exactly the row group at row_group_index(tf), filter by
    [from_ts, to_ts] inclusive, skip ts == SENTINEL_TS rows, return sorted
    by ts ascending."""
    table = pq.read_table(path, row_groups=[row_group_index(tf)])
    # extract ts/open/high/low/close/volume columns → list[Candle]
```

Mirrors Rust's `with_row_groups(vec![tf.row_group_index()])`. Reads only one TF's row group from disk; never loads the whole file.

### 3.3 `replay.py`

```python
class LoadedBars:
    """sym → tf → list[Candle]. Single source for 'what's in RAM'."""
    def get(self, symbol: str, tf: Timeframe) -> list[Candle] | None: ...
    def is_loaded(self, symbol: str, tf: Timeframe) -> bool: ...
    def last_ts(self, symbol: str, tf: Timeframe) -> int | None: ...
    def loaded_tfs(self, symbol: str) -> list[Timeframe]: ...
    def insert(self, symbol: str, tf: Timeframe, candles: list[Candle]) -> None: ...
    def extend(self, symbol: str, tf: Timeframe, more: list[Candle]) -> None: ...

@dataclass
class ReplayBuffer:
    loaded: LoadedBars
    symbol: str
    replay_ts: int
    stepping_tf: Timeframe

    def get_bars(self, tf: Timeframe) -> list[Candle]: ...   # completed + forming
    def step(self, direction: int) -> None: ...
    def seek(self, ts: int) -> None: ...

def bsearch(buf: list[Candle], target: int) -> int | None:
    """Last index where buf[i].ts <= target, or None."""
    # uses bisect.bisect_right then -1

def aggregate_forming(stepping_buf, from_ts: int, to_ts: int) -> Candle | None:
    """Build a single forming candle by aggregating stepping-TF bars in
    [from_ts, to_ts]. Returns None if no bars in range."""
```

`get_bars(tf)` semantics:
- If `tf.seconds <= stepping_tf.seconds`: return all bars up to and including the cursor (all are complete).
- Else: return completed bars before the current TF period, plus a forming bar built by `aggregate_forming` over the stepping-TF bars inside the current period.

### 3.4 `session.py`

```python
class Session:
    def __init__(self, data_dir: Path) -> None: ...
    def open(self, symbol: str, stepping_tf: Timeframe,
             range_from: int, range_to: int) -> None: ...
    def ensure_tf(self, symbol: str, tf: Timeframe) -> None: ...   # lazy load
    def step(self, direction: int) -> None: ...
    def seek(self, ts: int) -> None: ...
    def bars(self, symbol: str, tf: Timeframe) -> list[Candle]: ...
    def set_stepping_tf(self, tf: Timeframe) -> None: ...
    @property
    def replay_ts(self) -> int: ...
    @property
    def stepping_tf(self) -> Timeframe: ...
```

Owns a `ReplayBuffer` plus a `data_dir` and a `[range_from, range_to]` window. `ensure_tf` reads parquet on first access for `(symbol, tf)`. Forward `step()` calls `_maybe_extend()` which lazy-loads the next 14 days of bars when the cursor passes 80% of the buffer (matches Rust).

### 3.5 `engine/`

```python
# engine/events.py — per-variant dataclasses, union-typed
@dataclass(frozen=True, slots=True)
class NeedsRedraw: pass

@dataclass(frozen=True, slots=True)
class BarAppended: pane_id: int

@dataclass(frozen=True, slots=True)
class BarRemoved: pane_id: int

@dataclass(frozen=True, slots=True)
class BarUpdated: pane_id: int

@dataclass(frozen=True, slots=True)
class ViewportChanged: pane_id: int

EngineEvent = NeedsRedraw | BarAppended | BarRemoved | BarUpdated | ViewportChanged
```

```python
# engine/pane.py
@dataclass
class EnginePane:
    id: int
    symbol: str
    tf: Timeframe
    time: TimeAxis
    price: PriceAxis
    candles: list[Candle]

    def __init__(self, id: int, symbol: str, tf: Timeframe,
                 width: float, height: float) -> None: ...
    def rebuild(self, candles: list[Candle]) -> None: ...
    def resize(self, width: float, height: float) -> None: ...
    def zoom_by(self, delta: float) -> bool: ...
    def pan_time_by_px(self, px: float) -> bool: ...
    def pan_price_by_px(self, px: float) -> bool: ...
```

```python
# engine/__init__.py — AppEngine
class AppEngine:
    def __init__(self, data_dir: Path) -> None: ...
    def open(self, symbol: str, stepping_tf: Timeframe,
             range_from: int, range_to: int) -> None: ...
    @property
    def session(self) -> Session: ...
    @property
    def panes(self) -> list[EnginePane]: ...
    def pane(self, id: int) -> EnginePane | None: ...
    def pane_mut(self, id: int) -> EnginePane | None: ...
    def add_pane(self, symbol: str, tf: Timeframe,
                 width: float, height: float) -> int: ...
    def step(self, direction: int) -> list[EngineEvent]: ...
    def seek(self, ts: int) -> EngineEvent: ...
    def reload_all(self) -> None: ...

def classify_step(before: int, after: int, pane_id: int) -> EngineEvent:
    """Append > Remove > Update; equal lengths mean the last bar's OHLC
    was overwritten."""
```

Python doesn't enforce `&self`/`&mut self` like Rust, so `pane()` and `pane_mut()` are conventional aliases (both return the same mutable reference). Callers should treat the result of `pane()` as read-only by convention.

### 3.6 `chart/`

- **`ticks.py`** — `nice_step(raw: float)` returns 1, 2, or 5 × 10ⁿ. `price_ticks(min, max, height_px, target)` returns a list of `PriceTick(price, y_px)` at nice intervals. y_px is top-down (0 at price_max).
- **`time_ticks.py`** — `STEPS_SECS = (60, 300, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400, 172800, 604800, 2_592_000)`. `nice_step(raw: int)` returns smallest STEPS_SECS value ≥ raw, clamped to [first, last].
- **`price_axis.py`** — `PriceAxis(height, price_min, price_max)`, frozen `YProjection(price_scale, price_offset)` returned by `.projection()`. `y_for_price`, `price_for_y`, `fit_to(candles)` (5% padding), `pan_by_px`, `labels(pane_inner_width)`.
- **`time_axis.py`** — `TimeAxis(bar_width, base_index, width)`, frozen `XProjection(bar_spacing, x_offset)`. `x_for_bar`, `bar_for_x`, `visible_range`, `labels(candles, pane_inner_height)` emitting `TextPrim`s at calendar boundaries.
- **`scene.py`** — `TextPrim(text, x, y, size_px, color)` dataclass. The single label primitive both axes emit and the rasterizer consumes.

The projection objects are the contract shared with the rasterizer. Whatever paints a candle reads `(bar_spacing, x_offset, price_scale, price_offset)` from the projection — same single source of truth that the Rust WGSL shader uses, so CPU and (future) GPU paths cannot drift.

## 4. `datasmith` design

### 4.1 CLI surface

```
datasmith ingest databento SYM --source <dir> --out <dir>
datasmith ingest databento --all --source <dir> --out <dir>
```

Implemented with stdlib `argparse`:

- `datasmith` (top-level command) →
  - `ingest` (subcommand) →
    - `databento` (sub-subcommand) → flags `--source`, `--out`, `--all`, positional `SYM`.

Resolves `SYM` to `<source>/SYM/ohlcv-1m/*.dbn.zst` (matches magsi's directory convention). `--all` iterates every immediate subdirectory of `<source>` and ingests each.

### 4.2 `databento.py`

```python
def read_m1_bars(dbn_dir: Path) -> list[Candle]:
    """Decode every .dbn.zst under dbn_dir, return sorted-by-ts M1 bars."""
    files = sorted(dbn_dir.glob("*.dbn.zst"))
    bars: list[Candle] = []
    for path in files:
        store = databento.DBNStore.from_file(path)
        for rec in store:
            bars.append(Candle(
                ts=rec.ts_event // 1_000_000_000,        # nanos → seconds
                open=rec.open / 1_000_000_000,           # 1e-9 fixed → float
                high=rec.high / 1_000_000_000,
                low=rec.low / 1_000_000_000,
                close=rec.close / 1_000_000_000,
                volume=rec.volume,
            ))
    bars.sort(key=lambda c: c.ts)
    return bars
```

Two contracts ported from magsi: nanosecond → second division (`// 1_000_000_000`) and price scale `1e-9` (Databento ships fixed-point integers).

### 4.3 `aggregate.py`

```python
def aggregate(src: list[Candle], tf: Timeframe) -> list[Candle]:
    """Bucket finer-resolution bars into `tf` buckets. Caller pre-sorts src."""
    tf_secs = tf.seconds
    out: list[Candle] = []
    for bar in src:
        bucket_ts = (
            bar.ts - ((bar.ts - 259_200) % 604_800)   # weekly: align to Monday
            if tf is Timeframe.W1
            else (bar.ts // tf_secs) * tf_secs
        )
        if out and out[-1].ts == bucket_ts:
            last = out[-1]
            out[-1] = Candle(
                ts=last.ts, open=last.open,
                high=max(last.high, bar.high),
                low=min(last.low, bar.low),
                close=bar.close,
                volume=last.volume + bar.volume,
            )
        else:
            out.append(bar)
    return out
```

Frozen dataclasses → re-allocate on update. Same bucketing math as magsi's Rust `aggregate`.

### 4.4 `write.py`

```python
SCHEMA = pa.schema([
    pa.field("ts",        pa.int64(),   nullable=False),
    pa.field("timeframe", pa.string(),  nullable=False),
    pa.field("open",      pa.float64(), nullable=False),
    pa.field("high",      pa.float64(), nullable=False),
    pa.field("low",       pa.float64(), nullable=False),
    pa.field("close",     pa.float64(), nullable=False),
    pa.field("volume",    pa.int64(),   nullable=False),
])

SENTINEL_TS = -(2**63)   # i64::MIN — read side filters this out

def write_parquet(path: Path, by_tf: dict[Timeframe, list[Candle]]) -> None:
    """One row group per Timeframe in canonical ALL order. Empty TFs get a
    single sentinel row so row-group indices stay aligned with row_group_index()."""
    sentinel = [Candle(ts=SENTINEL_TS, open=0.0, high=0.0, low=0.0, close=0.0, volume=0)]
    with pq.ParquetWriter(path, SCHEMA, compression="snappy") as writer:
        for tf in Timeframe.ALL:
            candles = by_tf.get(tf) or sentinel
            batch = pa.RecordBatch.from_pydict({
                "ts":        [c.ts for c in candles],
                "timeframe": [str(tf)] * len(candles),
                "open":      [c.open for c in candles],
                "high":      [c.high for c in candles],
                "low":       [c.low for c in candles],
                "close":     [c.close for c in candles],
                "volume":    [c.volume for c in candles],
            }, schema=SCHEMA)
            writer.write_batch(batch)             # one batch per row group
```

Two contracts that bind reader and writer together:

1. **Row-group order is `core.candle.ALL`** — both sides import the same tuple.
2. **Empty-TF sentinel is `ts = -(2**63)`** — without it pyarrow drops the row group entirely and shifts later indices. The reader filters sentinels out.

### 4.5 Progress output

Use `tqdm` (small dep) for two progress bars: `read_m1_bars` over the file list, and `write_parquet` over `Timeframe.ALL`. Mirrors magsi's `indicatif` UX.

## 5. `tui` design

### 5.1 Screen ownership and run loop

Textual owns the layout (header / chart area / footer) and draws header and footer. The **chart cells** are claimed by a `ChartWidget` that draws nothing — instead, after each Textual frame flushes, the app writes Kitty escape sequences to stdout that paint an RGBA image into those cells.

Persistent image ID (single integer, same every frame) — each transmit replaces the previous image in place. No flicker, no per-frame delete.

Periodic global purge `\x1b_Ga=d,d=a;\x1b\\` every ~100 frames to keep the terminal's image cache bounded (matches `app-tui`'s strategy).

### 5.2 `kitty.py` — protocol encoder

```python
import zlib, base64

CHUNK_SIZE = 4096

class KittyEncoder:
    def transmit_image(self, out, rgba: bytes,
                       width: int, height: int,
                       image_id: int, cols: int, rows: int) -> None:
        compressed = zlib.compress(rgba, level=1)        # ~Compression::fast()
        encoded    = base64.b64encode(compressed)
        n = (len(encoded) + CHUNK_SIZE - 1) // CHUNK_SIZE
        for i, start in enumerate(range(0, len(encoded), CHUNK_SIZE)):
            chunk = encoded[start:start + CHUNK_SIZE]
            more = 1 if i + 1 < n else 0
            if i == 0:
                out.write(f"\x1b_Gf=32,s={width},v={height},a=T,t=d,"
                          f"i={image_id},o=z,c={cols},r={rows},q=2,m={more};".encode())
            else:
                out.write(f"\x1b_Gm={more};".encode())
            out.write(chunk)
            out.write(b"\x1b\\")
        out.flush()

def delete_image(out, image_id: int) -> None:
    out.write(f"\x1b_Ga=d,d=I,i={image_id};\x1b\\".encode())
    out.flush()
```

Same envelope as `huge-td/src/app-tui/src/kitty.rs`: format 32 (RGBA), zlib + base64, 4 KiB chunks with `m=1`/`m=0` continuation, `q=2` to suppress server replies.

### 5.3 `rasterizer.py` — Pillow

```python
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from core.engine.pane import EnginePane

# Co-located with the package; no `tui.fonts` subpackage needed.
JETBRAINS_MONO = Path(__file__).parent / "fonts" / "JetBrainsMono-Regular.ttf"

class Rasterizer:
    def __init__(self, width: int, height: int) -> None:
        self.image = Image.new("RGBA", (width, height), (20, 20, 28, 255))
        self.font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    def resize(self, w: int, h: int) -> None: ...
    def render(self, pane: EnginePane) -> bytes:
        """Paint candles + axis labels into self.image; return raw RGBA bytes."""
        draw = ImageDraw.Draw(self.image, "RGBA")
        # background
        # candle bodies + wicks using pane.time.projection() and pane.price.projection()
        # axis labels from pane.price.labels(...) and pane.time.labels(...)
        return self.image.tobytes()
```

The candle/wick math reads `(bar_spacing, x_offset, price_scale, price_offset)` straight from the engine's `XProjection` / `YProjection` — same single source of truth that the Rust WGSL shader uses.

JetBrains Mono TTF is bundled as a package data file under `tui/fonts/`.

### 5.4 `chart_widget.py` and `app.py`

```python
# chart_widget.py
class ChartWidget(Widget):
    """Reserves cells; emits a `ChartResized` message on layout change.
       Renders nothing — Kitty image is painted by the App after composition."""
```

```python
# app.py
class SikapApp(App):
    def __init__(self, parquet_path: Path, symbol: str, tf: Timeframe) -> None:
        super().__init__()
        self.engine = AppEngine(parquet_path.parent)
        self.engine.open(symbol, tf, range_from=0, range_to=2**63 - 1)
        self.pane_id = self.engine.add_pane(symbol, tf, width=1.0, height=1.0)
        self.rasterizer = Rasterizer(1, 1)
        self.kitty = KittyEncoder()
        self.cell_w, self.cell_h = detect_cell_size()
        self.image_id = 1
        self.frame_count = 0

    def compose(self):
        yield Header()
        yield ChartWidget(id="chart")
        yield Footer()

    def on_key(self, event):
        action = KEYMAP.get(event.key)
        if   action == "step_forward": self._step(+1)
        elif action == "step_back":    self._step(-1)
        elif action == "fit_content":  self._fit()
        elif action == "quit":         self.exit()

def main() -> None:
    """Entry point bound to the `sikap-tui` console script."""
    args = _parse_args()                     # --parquet PATH [--symbol SYM] [--tf TF]
    SikapApp(args.parquet, args.symbol, args.tf).run()
```

The chart is repainted in response to:

- engine state changes (after `step` / `fit` / etc.)
- `ChartResized` from `ChartWidget`

The exact Textual hook for "after the compositor flushes for this frame" (`on_idle`, `after_refresh`, etc.) is a v1 implementation detail — see Open questions.

### 5.5 `input.py` — flat keymap

```python
KEYMAP = {
    "right": "step_forward", "l": "step_forward",
    "left":  "step_back",    "h": "step_back",
    "f":     "fit_content",  "r": "fit_content",
    "q":     "quit",         "ctrl+c": "quit",
}
```

No modal modes (no TF entry, no draw mode, no Ctrl+W chord) for v1 — those land with the features they enable.

### 5.6 Cell-size detection

```python
import fcntl, termios, struct, sys

def detect_cell_size() -> tuple[int, int]:
    try:
        ws = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ,
                         struct.pack("HHHH", 0, 0, 0, 0))
        rows, cols, xpix, ypix = struct.unpack("HHHH", ws)
        if rows and cols and xpix and ypix:
            return xpix // cols, ypix // rows
    except OSError:
        pass
    return 8, 16
```

Same `TIOCGWINSZ` ioctl trick as `app-tui/src/main.rs`. Used to convert chart cell dimensions into pixel dimensions for the rasterizer.

## 6. Testing strategy

### 6.1 `core` (pytest, `tests/` next to package)

| Test | What it pins |
|---|---|
| `test_candle.py` | `Timeframe.ALL` contains every variant exactly once; `row_group_index(tf)` round-trips through `ALL`. |
| `test_replay.py` | `LoadedBars.get/insert/extend/loaded_tfs` semantics; `bsearch` boundaries (empty buf, target before first, target equal to last); `aggregate_forming` shape (open from first, high=max, low=min, close from last, sum volume); `ReplayBuffer.get_bars` behavior across the `tf <= stepping_tf` and `tf > stepping_tf` branches. |
| `test_session.py` | (with a tiny written-on-the-fly parquet) `open` sets cursor correctly; `step(+1)` advances exactly one stepping-TF bar; `step(-1)` reverses; `seek` clamps to range. |
| `test_chart_ticks.py` | `nice_step` picks 1/2/5; `price_ticks` stays inside range and higher prices map to lower y_px. |
| `test_chart_time_ticks.py` | `nice_step` picks next-higher in `STEPS_SECS`, floors below min, caps above max. |
| `test_chart_price_axis.py` | `fit_to` is padded above/below; `pan_by_px` shifts in screen direction. |
| `test_chart_time_axis.py` | `x_for_bar` matches `XProjection`; `visible_range` returns inclusive (first, last); labels emit at calendar boundaries. |
| `test_engine_pane.py` | `rebuild` snaps `base_index` to last bar; `zoom_by` clamps; `pan_time_by_px` clamps. |
| `test_engine.py` | `classify_step` branches (Append/Remove/Update). |

### 6.2 `datasmith`

| Test | What it pins |
|---|---|
| `test_aggregate.py` | Hourly bucketing of M1 bars (24h fixture); weekly bucketing aligns to Monday for a known timestamp; volume sums; high/low aggregate correctly. |
| `test_write_read_roundtrip.py` | **Cross-package contract test.** Build a `dict[Timeframe, list[Candle]]` with bars in some TFs and not others, write via `datasmith.write.write_parquet`, read back through `core.storage.read_candles` for every TF, assert empty TFs return `[]` and populated TFs round-trip exactly. If row-group order or sentinel logic ever drifts, this fails loudly. |

### 6.3 `tui`

| Test | What it pins |
|---|---|
| `test_kitty_encoder.py` | Encode a 2×2 RGBA fixture; assert the output bytes match an expected envelope (correct header, `o=z`, exactly one chunk for tiny payload, terminating `m=0`). |
| `test_rasterizer_smoke.py` | Render 10 candles at 200×100; assert the resulting RGBA bytes are non-uniform (rasterizer drew *something*) and length matches `w*h*4`. |

No Textual integration tests — the harness cost outweighs the benefit at v1.

### 6.4 Frameworks and ergonomics

- **pytest** as the test runner across all three projects.
- **No hypothesis** (per user choice).
- Each project's `pyproject.toml` includes `[dependency-groups] dev = ["pytest"]` (or a `[tool.uv.dev-dependencies]` section). `uv run pytest` from each project root.

## 7. Dev workflow

Three independent uv projects, three `.venv`s:

```
$ cd src/core      && uv venv && uv sync && uv run pytest
$ cd src/datasmith && uv venv && uv sync && uv run pytest
$ cd src/tui       && uv venv && uv sync && uv run pytest
```

`core` is installed editable into `datasmith/.venv` and `tui/.venv` via `[tool.uv.sources] core = { path = "../core", editable = true }`. Editing `core/core/*.py` is reflected immediately in both consumers.

A future top-level `Makefile` or `justfile` may wrap "run all tests" / "build datasmith" / "run TUI" — out of scope for v1.

## 8. Open questions and risks

1. **Textual render hook.** The exact integration point for "after the compositor flushes, write Kitty escapes" needs validation during implementation. Candidates: `on_idle`, `after_refresh`, a custom timer driver, or a manual `self.refresh()` followed by direct stdout writes from the same `on_*` handler that triggered the change. Plan: prototype against Textual's API early in the implementation plan; if no clean hook exists, fall back to writing the Kitty image right after each engine state change (skip the "after Textual flushes" coupling).

2. **Pillow performance.** Pillow's `ImageDraw` is pure-Python in the hot path and may be slow for very large panes (e.g. > 2000 candles × wide chart). Mitigation: numpy-array slicing for bulk fills if profiling shows a bottleneck; Pillow is fast enough for typical pane sizes.

3. **Editable install across three venvs.** Two copies of `core` are installed editable. Both point at the same source tree so there's no real divergence, but if a developer creates a fourth venv that pip-installs `core` non-editable, the divergence becomes possible. Mitigation: documented dev workflow; not enforced.

4. **Databento SDK weight.** The `databento` package on PyPI brings in numpy and other heavy deps. Acceptable since `datasmith` is a one-shot tool, but adds install time. No mitigation needed.

5. **Single-pane keymap conflicts with future modes.** The flat `KEYMAP` dict is fine for v1, but TF-entry mode (1-9 entering a popup) will need modal dispatch later. Plan: introduce a `Mode` abstraction when the TF-entry feature lands, not before.
