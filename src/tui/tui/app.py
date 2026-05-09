"""sikap-tui — Textual app, single-pane chart replay.

Usage: sikap-tui --parquet PATH [--symbol SYM] [--tf TF]

If --symbol is omitted, derives it from the parquet filename (stem).
If --tf is omitted, defaults to 15m.
"""

from __future__ import annotations

import argparse
import math
import sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from core.candle import Timeframe
from core.engine import AppEngine
from core.engine.pane import EnginePane
from core.indicators.vwap import compute_vwap, globex_daily_anchor, vwap_overlay

import native

from tui.chart_widget import ChartWidget
from tui.input import detect_cell_size
from tui.rasterizer import Rasterizer


_IMAGE_ID       = 1
_PURGE_EVERY    = 100
# Multiplicative wheel zoom: each notch scales bar_width by this factor.
# 1.1 ≈ 10% per notch — feels natural across the bar_width [2, 64] range.
_ZOOM_FACTOR    = 1.1
# Render at 1/RENDER_DIVISOR of the terminal pixel resolution. Kitty
# would otherwise scale a smaller image up to fill the chart cells, which
# softens lines + axis labels. With the Rust rasterizer we have the
# headroom to render at native pixel density (=1) and ship sharp output.
# Bump to 2 or 3 only if you need the FPS on a very large terminal.
_RENDER_DIVISOR = 1
_TICK_HZ        = 60.0          # render loop frequency for dirty flag
# Reserve space inside the rasterizer buffer for axis labels. The candle
# drawing area is the buffer minus these gutters; price labels sit in the
# right gutter, time labels in the bottom gutter.
_PRICE_GUTTER_PX = 56
_TIME_GUTTER_PX  = 20
# Per-pixel multiplicative factor for axis-drag zoom. ~0.3% per pixel
# means a 100-pixel drag scales by ~1.35×, which feels right.
_AXIS_ZOOM_PER_PX = 1.003


class SikapApp(App):
    CSS = """
    Screen { background: #131722; }
    """

    BINDINGS = [
        ("right,l",   "step(1)",  "step forward"),
        ("left,h",    "step(-1)", "step back"),
        ("f,r",       "fit",      "fit content"),
        ("q,ctrl+c",  "quit",     "quit"),
    ]

    def __init__(
        self,
        parquet_path: Path,
        symbol: str,
        tf: Timeframe,
        view_from: int = 0,
        view_to:   int = 2**63 - 1,
    ) -> None:
        super().__init__()
        self._parquet_path = parquet_path
        self._symbol       = symbol
        self._tf           = tf
        self._view_from    = view_from
        self._view_to      = view_to
        self._engine       = AppEngine(parquet_path.parent)
        # Engine extent is always the full parquet — `view_from`/`view_to`
        # only position the initial cursor + visible window, so the user
        # can pan past either edge.
        self._engine.open(symbol, tf, range_from=0, range_to=2**63 - 1)
        # Seek to the right edge of the requested view (defaults to end of
        # data when no --to given).
        self._engine.session.seek(view_to)
        self._pane_id      = self._engine.add_pane(symbol, tf, width=1.0, height=1.0)
        self._rasterizer   = Rasterizer(1, 1)
        self._cell_w, self._cell_h = detect_cell_size()
        self._frame_count  = 0
        self._last_region  = None        # detect first/changed layout
        self._initial_fit_done = False   # refit price on first real geometry
        # Drag state. Anchor is the previous mouse position in screen cells;
        # mode is one of "pan" (chart area), "price_zoom" (right gutter),
        # "time_zoom" (bottom gutter). None = not dragging.
        self._drag_anchor: tuple[int, int] | None = None
        self._drag_mode:   str | None             = None
        # Chart-pixels per terminal cell; recomputed on geometry change.
        # These convert mouse-cell deltas to engine pixel coords.
        self._px_per_cell_x = self._cell_w / _RENDER_DIVISOR
        self._px_per_cell_y = self._cell_h / _RENDER_DIVISOR
        # Dirty flag: mouse handlers set this; the render loop drains it.
        # Decouples input rate (60–120 Hz) from render rate (30 Hz).
        self._dirty = False
        # Textual replaces sys.stdout with a print-capture; write Kitty escapes
        # directly to the TTY to bypass it.
        self._tty = open("/dev/tty", "wb", buffering=0)

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChartWidget(id="chart")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0 / _TICK_HZ, self._tick_render)

    def _tick_render(self) -> None:
        chart = self.query_one("#chart", ChartWidget)
        region = chart.region
        if region.width <= 0 or region.height <= 0:
            return
        rkey = (region.x, region.y, region.width, region.height)
        if rkey != self._last_region:
            self._last_region = rkey
            self._on_geometry_changed(region)
            return
        if self._dirty:
            self._dirty = False
            self._render_chart(region)

    # ── Geometry / data lifecycle ────────────────────────────────

    def _fit_initial_view(self, pane: EnginePane) -> None:
        """Position the time axis to cover [view_from, view_to]. Called once
        on the first geometry callback. Skipped when both args are at their
        defaults (we then keep the engine's standard 'last bar at right edge'
        behavior with default zoom)."""
        candles = pane.candles
        if not candles:
            return
        if self._view_from == 0 and self._view_to == 2**63 - 1:
            return  # neither bound supplied
        # Map ts → bar index. bisect over a flat ts list is cheaper than
        # passing key= per call, and we only build it once.
        ts_list = [c.ts for c in candles]
        i_to_excl = bisect_right(ts_list, self._view_to)
        if i_to_excl == 0:
            return  # everything is past view_to
        i_to = i_to_excl - 1
        i_from = bisect_left(ts_list, self._view_from)
        if i_from > i_to:
            i_from = i_to
        visible_bars = max(1, i_to - i_from + 1)
        # Same [2, 64] clamp as zoom_bar_width — keeps the chart legible if
        # the user requests an extreme window.
        pane.time.bar_width  = max(2.0, min(64.0, pane.time.width / visible_bars))
        pane.time.base_index = float(i_to)
        # Refit price to the new visible range (resize() triggers it via the
        # public API, no private call needed).
        pane.resize(pane.time.width, pane.price.height)

    def _attach_indicators(self, pane: EnginePane) -> None:
        """Recompute and attach indicator overlays for a freshly rebuilt
        pane. Cheap enough to run on every rebuild — the hot loop is in
        Rust and the candle list is the only input."""
        if not pane.candles:
            pane.overlays = []
            return
        # Visual default mirrors TradingView's session-VWAP indicator on
        # an ES chart in ETH mode: Anchor = Session (Globex daily reset
        # at 18:00 ET) and Bands Multiplier #1 = 1. Strategy code passes
        # k_bands=(1.0, 1.75, 2.0) when the full algorithms.md surface
        # is needed.
        result = compute_vwap(pane.candles, anchor=globex_daily_anchor,
                              k_bands=(1.0,))
        pane.overlays = [vwap_overlay(result)]

    def _on_geometry_changed(self, region) -> None:
        """Layout/size changed. Resize axes geometrically; preserve pan/zoom
        state. On first real geometry, also refit price (we can't fit
        before we know the size)."""
        chart_pixel_w = max(1, (region.width  * self._cell_w) // _RENDER_DIVISOR)
        chart_pixel_h = max(1, (region.height * self._cell_h) // _RENDER_DIVISOR)
        # Mouse-cell → engine-pixel scale (so a 1-cell drag pans the same
        # amount regardless of render resolution).
        self._px_per_cell_x = chart_pixel_w / max(1, region.width)
        self._px_per_cell_y = chart_pixel_h / max(1, region.height)
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        # Pane axes describe the candle drawing area, not the buffer; the
        # right/bottom gutters hold the axis labels.
        pane.time.resize(max(1, chart_pixel_w - _PRICE_GUTTER_PX))
        pane.price.resize(max(1, chart_pixel_h - _TIME_GUTTER_PX))
        if not self._initial_fit_done:
            bars = self._engine.session.bars(self._symbol, self._tf)
            pane.rebuild(bars)
            self._fit_initial_view(pane)
            self._attach_indicators(pane)
            self._initial_fit_done = True
        self._render_chart(region)

    # ── Actions ──────────────────────────────────────────────────

    def action_step(self, direction: int) -> None:
        # AppEngine.step() rebuilds panes internally — no extra rebuild needed.
        self._engine.step(direction)
        for pane in self._engine.panes:
            self._attach_indicators(pane)
        self._dirty = True

    def action_fit(self) -> None:
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        bars = self._engine.session.bars(self._symbol, self._tf)
        pane.rebuild(bars)
        self._attach_indicators(pane)
        self._dirty = True

    # ── Mouse: drag-pan + axis-drag zoom + wheel-zoom ────────────

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        chart = self.query_one("#chart", ChartWidget)
        region = chart.region
        if not region.contains(event.x, event.y):
            return

        # Hit-test gutters in cell coords. Gutters are sized in chart pixels,
        # so divide by cell size to get cell extents.
        gutter_cells_x = max(1, math.ceil(_PRICE_GUTTER_PX / self._cell_w))
        gutter_cells_y = max(1, math.ceil(_TIME_GUTTER_PX  / self._cell_h))
        local_x = event.x - region.x
        local_y = event.y - region.y
        in_right_gutter  = local_x >= region.width  - gutter_cells_x
        in_bottom_gutter = local_y >= region.height - gutter_cells_y

        if in_right_gutter and not in_bottom_gutter:
            self._drag_mode = "price_zoom"
        elif in_bottom_gutter and not in_right_gutter:
            self._drag_mode = "time_zoom"
        else:
            self._drag_mode = "pan"

        self._drag_anchor = (event.x, event.y)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_anchor is None or self._drag_mode is None:
            return
        last_x, last_y = self._drag_anchor
        dx_cells = event.x - last_x
        dy_cells = event.y - last_y
        if dx_cells == 0 and dy_cells == 0:
            return
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return

        if self._drag_mode == "pan":
            if dx_cells != 0:
                pane.pan_time_by_px(dx_cells * self._px_per_cell_x)
            if dy_cells != 0:
                pane.pan_price_by_px(dy_cells * self._px_per_cell_y)
        elif self._drag_mode == "price_zoom":
            # Drag DOWN on the price gutter → expand the visible price
            # range (compress the chart vertically). Center stays put.
            dy_px  = dy_cells * self._cell_h
            factor = _AXIS_ZOOM_PER_PX ** dy_px
            pane.price.zoom_around_center(factor)
        elif self._drag_mode == "time_zoom":
            # Drag RIGHT on the time gutter → bars get wider (zoom in).
            dx_px  = dx_cells * self._cell_w
            factor = _AXIS_ZOOM_PER_PX ** dx_px
            pane.time.zoom_bar_width(factor)

        self._drag_anchor = (event.x, event.y)
        self._dirty = True

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if event.button == 1:
            self._drag_anchor = None
            self._drag_mode   = None

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._zoom(_ZOOM_FACTOR)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._zoom(1.0 / _ZOOM_FACTOR)

    def _zoom(self, factor: float) -> None:
        """Multiplicative wheel zoom. Each notch scales bar_width by `factor`,
        giving consistent perceptual steps across the [2, 64] range."""
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        delta = pane.time.bar_width * (factor - 1.0)
        if pane.zoom_by(delta):
            self._dirty = True

    # ── Render ───────────────────────────────────────────────────

    def _render_chart(self, region) -> None:
        """Paint the current pane state. No engine state mutation."""
        chart_pixel_w = max(1, (region.width  * self._cell_w) // _RENDER_DIVISOR)
        chart_pixel_h = max(1, (region.height * self._cell_h) // _RENDER_DIVISOR)
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        self._rasterizer.resize(chart_pixel_w, chart_pixel_h)
        rgba = self._rasterizer.render(pane)

        # Move cursor to chart cell origin (1-based for ANSI), then transmit.
        self._tty.write(f"\x1b[{region.y + 1};{region.x + 1}H".encode())
        self._tty.write(native.encode_image(
            rgba, chart_pixel_w, chart_pixel_h,
            _IMAGE_ID, region.width, region.height,
        ))

        self._frame_count += 1
        if self._frame_count % _PURGE_EVERY == 0:
            self._tty.write(native.delete_all_images_seq())


def _parse_ts(s: str) -> int:
    """Parse ISO 8601 date or datetime to unix seconds. Naive inputs are
    interpreted as UTC. Accepts e.g. `2025-12-22`, `2025-12-22T18:00`,
    `2025-12-22T18:00:00+00:00`."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sikap-tui")
    parser.add_argument("--parquet", type=Path, required=True,
                        help="path to a canonical parquet file")
    parser.add_argument("--symbol", help="symbol name (default: parquet stem)")
    parser.add_argument("--tf", default="15m",
                        help="timeframe (default: 15m)")
    parser.add_argument("--from", dest="view_from", type=_parse_ts, default=0,
                        help="ISO date/datetime; left edge of initial view (data outside is still loadable)")
    parser.add_argument("--to",   dest="view_to",   type=_parse_ts, default=2**63 - 1,
                        help="ISO date/datetime; right edge of initial view + cursor seek target")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.parquet.exists():
        print(f"parquet not found: {args.parquet}", file=sys.stderr)
        return 1

    symbol = args.symbol or args.parquet.stem
    tf = Timeframe.from_str(args.tf)

    SikapApp(args.parquet, symbol, tf,
             view_from=args.view_from, view_to=args.view_to).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
