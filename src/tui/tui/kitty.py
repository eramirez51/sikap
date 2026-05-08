"""Kitty graphics protocol encoder. Transmits RGBA images as zlib+base64
chunks wrapped in APC sequences.

Spec: https://sw.kovidgoyal.net/kitty/graphics-protocol/
"""

from __future__ import annotations

import base64
import zlib
from typing import IO


CHUNK_SIZE = 4096


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
        compressed = zlib.compress(rgba, level=1)        # ~Compression::fast()
        encoded    = base64.b64encode(compressed)
        n_chunks   = (len(encoded) + CHUNK_SIZE - 1) // CHUNK_SIZE

        for i, start in enumerate(range(0, len(encoded), CHUNK_SIZE)):
            chunk = encoded[start : start + CHUNK_SIZE]
            more  = 1 if i + 1 < n_chunks else 0
            if i == 0:
                header = (
                    f"\x1b_Gf=32,s={width},v={height},a=T,t=d,"
                    f"i={image_id},o=z,c={cols},r={rows},q=2,m={more};"
                ).encode()
            else:
                header = f"\x1b_Gm={more};".encode()
            out.write(header)
            out.write(chunk)
            out.write(b"\x1b\\")
        out.flush()


def delete_image(out: IO[bytes], image_id: int) -> None:
    """Delete a previously transmitted image by ID."""
    out.write(f"\x1b_Ga=d,d=I,i={image_id};\x1b\\".encode())
    out.flush()


def delete_all_images(out: IO[bytes]) -> None:
    """Purge every Kitty image from the terminal's cache."""
    out.write(b"\x1b_Ga=d,d=a;\x1b\\")
    out.flush()
