"""Renderer-agnostic scene primitives shared between axes and rasterizers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextPrim:
    """A renderer-bound text primitive in pane-local pixel coordinates.
    `(x, y)` is the anchor point; the renderer chooses how to interpret it
    (typically top-left)."""

    text:    str
    x:       float
    y:       float
    size_px: float
    color:   tuple[float, float, float, float]   # RGBA in 0.0..1.0
