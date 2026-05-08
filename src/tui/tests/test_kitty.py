import io
import zlib
import base64

from tui.kitty import KittyEncoder


def test_transmit_image_emits_one_chunk_for_small_payload():
    out = io.BytesIO()
    rgba = bytes([255, 0, 0, 255] * 4)              # 2×2 red
    enc = KittyEncoder()
    enc.transmit_image(out, rgba, width=2, height=2,
                       image_id=42, cols=1, rows=1)

    data = out.getvalue()
    # Expect a single APC sequence starting with \x1b_G... and ending with \x1b\\
    assert data.startswith(b"\x1b_G")
    assert data.endswith(b"\x1b\\")
    # Header should include i=42, s=2, v=2, m=0 (last/only chunk)
    header_end = data.index(b";")
    header = data[:header_end].decode()
    assert "i=42" in header
    assert "s=2"  in header
    assert "v=2"  in header
    assert "m=0"  in header
    assert "o=z"  in header   # zlib compression
    assert "f=32" in header   # RGBA32

    # Body should be valid base64-of-zlib of the original rgba.
    body = data[header_end + 1 : -2]                # strip header `;` and trailing `\x1b\\`
    decoded = zlib.decompress(base64.b64decode(body))
    assert decoded == rgba


def test_transmit_image_chunks_large_payload():
    """Random RGBA bytes don't compress, so a 64 KB payload definitely
    spans multiple 4096-byte chunks after zlib + base64."""
    import os
    out = io.BytesIO()
    rgba = os.urandom(64 * 1024)        # 64 KB random — incompressible
    KittyEncoder().transmit_image(out, rgba, 128, 128,
                                   image_id=1, cols=10, rows=10)
    data = out.getvalue()
    # Multiple chunks → at least one m=1 followed by a final m=0.
    assert b"m=1" in data
    assert data.endswith(b"\x1b\\")
