"""sikap-tui — Textual app, single-pane chart replay.

Usage: sikap-tui --parquet PATH [--symbol SYM] [--tf TF]

If --symbol is omitted, derives it from the parquet filename (stem).
If --tf is omitted, defaults to 15m.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from core.candle import Timeframe
from core.engine import AppEngine

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

    def __init__(self, parquet_path: Path, symbol: str, tf: Timeframe) -> None:
        super().__init__()
        self._parquet_path = parquet_path
        self._symbol       = symbol
        self._tf           = tf
        self._engine       = AppEngine(parquet_path.parent)
        self._engine.open(symbol, tf, range_from=0, range_to=2**63 - 1)
        # Seek cursor to the end of the data so the chart preloads with all
        # candles instead of just the first bar (replay starts at the end).
        self._engine.session.seek(2**63 - 1)
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
            self._initial_fit_done = True
        self._render_chart(region)

    # ── Actions ──────────────────────────────────────────────────

    def action_step(self, direction: int) -> None:
        # AppEngine.step() rebuilds panes internally — no extra rebuild needed.
        self._engine.step(direction)
        self._dirty = True

    def action_fit(self) -> None:
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        bars = self._engine.session.bars(self._symbol, self._tf)
        pane.rebuild(bars)
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
            dy_px = dy_cells * self._cell_h
            factor = _AXIS_ZOOM_PER_PX ** dy_px
            center = (pane.price.price_min + pane.price.price_max) * 0.5
            half   = (pane.price.price_max - pane.price.price_min) * 0.5 * factor
            pane.price.price_min = center - half
            pane.price.price_max = center + half
        elif self._drag_mode == "time_zoom":
            # Drag RIGHT on the time gutter → bars get wider (zoom in).
            dx_px  = dx_cells * self._cell_w
            factor = _AXIS_ZOOM_PER_PX ** dx_px
            new_bw = max(2.0, min(64.0, pane.time.bar_width * factor))
            pane.time.bar_width = new_bw

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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sikap-tui")
    parser.add_argument("--parquet", type=Path, required=True,
                        help="path to a canonical parquet file")
    parser.add_argument("--symbol", help="symbol name (default: parquet stem)")
    parser.add_argument("--tf", default="15m",
                        help="timeframe (default: 15m)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.parquet.exists():
        print(f"parquet not found: {args.parquet}", file=sys.stderr)
        return 1

    symbol = args.symbol or args.parquet.stem
    tf = Timeframe.from_str(args.tf)

    SikapApp(args.parquet, symbol, tf).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
