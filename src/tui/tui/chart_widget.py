"""A Textual Widget that reserves cells for the chart but draws nothing.

The chart image is painted by the App after each Textual frame flushes —
this widget just tells the layout engine how big the chart area is and
exposes its `region` (cell rect) for the App to use as a positioning anchor.
"""

from __future__ import annotations

from textual.widget import Widget


class ChartWidget(Widget):
    """Reserves space; renders nothing.

    The host app reads `self.region` (a `textual.geometry.Region` with
    `x, y, width, height` in cells) and writes Kitty escape sequences that
    paint an RGBA image into those cells.
    """

    DEFAULT_CSS = """
    ChartWidget {
        width: 1fr;
        height: 1fr;
    }
    """

    def render(self) -> str:                  # noqa: D401
        # Empty render — Textual will not draw over our Kitty image. The
        # cells stay "blank" from Textual's perspective, but the terminal
        # paints whatever Kitty image we transmit at those coordinates.
        return ""
