"""CPU rasterizer: paints candles + axis labels into an RGBA buffer ready
for Kitty transmission.

Reads `(bar_spacing, x_offset, price_scale, price_offset)` directly from the
engine's `XProjection` / `YProjection` — same single source of truth that the
Rust WGSL shader uses, so CPU and (future) GPU paths cannot drift.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.chart.scene import TextPrim
from core.engine.pane import EnginePane


_BG_COLOR     = (20, 20, 28, 255)
_BULL_COLOR   = (38, 166, 154, 255)
_BEAR_COLOR   = (239, 83, 80, 255)
_BODY_FRAC    = 0.8                                     # body width = 0.8 × bar_spacing

# Co-located with the package; no `tui.fonts` subpackage needed.
_FONT_PATH = Path(__file__).parent / "fonts" / "JetBrainsMono-Regular.ttf"


class Rasterizer:
    def __init__(self, width: int, height: int) -> None:
        self.width  = max(1, width)
        self.height = max(1, height)
        self.image: Image.Image  = Image.new("RGBA", (self.width, self.height), _BG_COLOR)
        self._font_cache: dict[int, ImageFont.FreeTypeFont] = {}

    def resize(self, width: int, height: int) -> None:
        width  = max(1, width)
        height = max(1, height)
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.image = Image.new("RGBA", (width, height), _BG_COLOR)

    def render(self, pane: EnginePane) -> bytes:
        # Background
        draw = ImageDraw.Draw(self.image, "RGBA")
        draw.rectangle([0, 0, self.width - 1, self.height - 1], fill=_BG_COLOR)

        x_proj = pane.time.projection()
        y_proj = pane.price.projection()

        for i, c in enumerate(pane.candles):
            cx = i * x_proj.bar_spacing + x_proj.x_offset
            body_w = pane.time.bar_width * _BODY_FRAC

            up    = c.close >= c.open
            color = _BULL_COLOR if up else _BEAR_COLOR

            body_top    = c.open  * y_proj.price_scale + y_proj.price_offset
            body_bottom = c.close * y_proj.price_scale + y_proj.price_offset
            wick_top    = c.high  * y_proj.price_scale + y_proj.price_offset
            wick_bottom = c.low   * y_proj.price_scale + y_proj.price_offset

            top, bot = sorted((body_top, body_bottom))
            draw.rectangle([cx - body_w / 2, top, cx + body_w / 2, bot], fill=color)
            draw.line([(cx, wick_top), (cx, wick_bottom)], fill=color, width=1)

        # Axis labels — engine emits TextPrims, we just place them.
        for tp in pane.price.labels(self.width):
            self._draw_text(draw, tp)
        for tp in pane.time.labels(pane.candles, self.height):
            self._draw_text(draw, tp)

        return self.image.tobytes()

    def _draw_text(self, draw: ImageDraw.ImageDraw, tp: TextPrim) -> None:
        size = max(1, int(tp.size_px))
        font = self._font_cache.get(size)
        if font is None:
            font = ImageFont.truetype(str(_FONT_PATH), size)
            self._font_cache[size] = font
        rgba = tuple(int(c * 255) for c in tp.color)
        draw.text((tp.x, tp.y), tp.text, fill=rgba, font=font)
