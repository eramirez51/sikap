"""sikap-tui — Textual app, single-pane chart replay.

Usage: sikap-tui --parquet PATH [--symbol SYM] [--tf TF]

If --symbol is omitted, derives it from the parquet filename (stem).
If --tf is omitted, defaults to 15m.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from core.candle import Timeframe
from core.engine import AppEngine

from tui.chart_widget import ChartWidget
from tui.input import detect_cell_size
from tui.kitty import KittyEncoder, delete_all_images
from tui.rasterizer import Rasterizer


_IMAGE_ID = 1
_PURGE_EVERY = 100


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
        self._pane_id      = self._engine.add_pane(symbol, tf, width=1.0, height=1.0)
        self._rasterizer   = Rasterizer(1, 1)
        self._kitty        = KittyEncoder()
        self._cell_w, self._cell_h = detect_cell_size()
        self._frame_count  = 0
        self._last_region  = None        # so we re-render after first layout

    def compose(self) -> ComposeResult:
        yield Header()
        yield ChartWidget(id="chart")
        yield Footer()

    def on_mount(self) -> None:
        # Force one render after the first layout completes.
        self.set_interval(0.1, self._tick_render)

    def _tick_render(self) -> None:
        chart = self.query_one("#chart", ChartWidget)
        region = chart.region
        if region.width <= 0 or region.height <= 0:
            return
        # Skip when nothing has changed.
        rkey = (region.x, region.y, region.width, region.height)
        if rkey == self._last_region:
            return
        self._last_region = rkey
        self._render_chart(region)

    # ── Actions ──────────────────────────────────────────────────

    def action_step(self, direction: int) -> None:
        self._engine.step(direction)
        chart = self.query_one("#chart", ChartWidget)
        self._render_chart(chart.region)

    def action_fit(self) -> None:
        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        # Re-fit by rebuilding from session bars (resets base_index + price).
        bars = self._engine.session.bars(self._symbol, self._tf)
        pane.rebuild(bars)
        chart = self.query_one("#chart", ChartWidget)
        self._render_chart(chart.region)

    # ── Render ───────────────────────────────────────────────────

    def _render_chart(self, region) -> None:
        chart_pixel_w = max(1, region.width  * self._cell_w)
        chart_pixel_h = max(1, region.height * self._cell_h)

        pane = self._engine.pane(self._pane_id)
        if pane is None:
            return
        pane.resize(chart_pixel_w, chart_pixel_h)
        # Re-fit price axis to the (possibly new) viewport.
        bars = self._engine.session.bars(self._symbol, self._tf)
        pane.rebuild(bars)

        self._rasterizer.resize(chart_pixel_w, chart_pixel_h)
        rgba = self._rasterizer.render(pane)

        out = sys.stdout.buffer
        # Move cursor to chart cell origin (1-based for ANSI).
        out.write(f"\x1b[{region.y + 1};{region.x + 1}H".encode())
        self._kitty.transmit_image(
            out, rgba, chart_pixel_w, chart_pixel_h,
            image_id=_IMAGE_ID, cols=region.width, rows=region.height,
        )

        self._frame_count += 1
        if self._frame_count % _PURGE_EVERY == 0:
            delete_all_images(out)


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
