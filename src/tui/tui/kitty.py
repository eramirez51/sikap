"""Kitty graphics protocol — thin Python wrapper around the native encoder.

The whole zlib + base64 + chunked-APC pipeline lives in `native::encode_image`
now; this file is just the small ergonomic surface (a class with a writer
arg + the two delete helpers).
"""

from __future__ import annotations

from typing import IO

import native


class KittyEncoder:
    """Stateless transmitter of RGBA images to a Kitty-compatible terminal."""

    def transmit_image(
        self,
        out: IO[bytes],
        rgba:    bytes,
        width:   int,
        height:  int,
        image_id: int,
        cols:    int,
        rows:    int,
    ) -> None:
        """Write a single image to `out`. Caller positions the cursor first."""
        payload = native.encode_image(rgba, width, height, image_id, cols, rows)
        out.write(payload)
        out.flush()


def delete_image(out: IO[bytes], image_id: int) -> None:
    out.write(native.delete_image_seq(image_id))
    out.flush()


def delete_all_images(out: IO[bytes]) -> None:
    out.write(native.delete_all_images_seq())
    out.flush()
