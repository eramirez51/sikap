"""Terminal cell-size detection."""

from __future__ import annotations

import fcntl
import struct
import sys
import termios


def detect_cell_size() -> tuple[int, int]:
    """Query terminal cell pixel dimensions via TIOCGWINSZ ioctl.
    Falls back to (8, 16) if the terminal doesn't report pixel size."""
    try:
        ws = fcntl.ioctl(
            sys.stdout.fileno(),
            termios.TIOCGWINSZ,
            struct.pack("HHHH", 0, 0, 0, 0),
        )
        rows, cols, xpix, ypix = struct.unpack("HHHH", ws)
        if rows and cols and xpix and ypix:
            return xpix // cols, ypix // rows
    except OSError:
        pass
    return 8, 16
