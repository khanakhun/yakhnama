"""Decoding limits for untrusted images (decompression-bomb protection).

An uploaded image is attacker-controlled: a few kilobytes of PNG or WebP can
declare a canvas of billions of pixels, or thousands of animation frames, and
decoding it would exhaust memory. The stripper therefore checks what the header
declares **before decoding any pixel** and refuses anything above these limits.

Pillow's own guard warns above ``Image.MAX_IMAGE_PIXELS`` and raises only above
twice that. The limit is set explicitly here, and ``exceeded_limit`` flags
everything above it for rejection, so the band where Pillow would only warn is an
error too. The warning filter is not changed process-wide on purpose: filters are
global and not thread-safe, and stripping runs in a worker thread.

All three limits are **proposed** (open question Q-M19).

Patterns: Adapter + Anti-Corruption Layer (limits of the Pillow boundary).
"""

import struct
from typing import Final

from PIL import Image, UnidentifiedImageError

MAX_FRAME_PIXELS: Final = 50_000_000
"""Largest canvas of one frame: 50 MP covers 48 and 50 MP phone cameras."""

MAX_FRAMES: Final = 200
"""Most frames an animated PNG or WebP may have."""

MAX_TOTAL_PIXELS: Final = 100_000_000
"""Largest width x height x frames decoded for one file (about 400 MB as RGBA)."""

# Set explicitly rather than inheriting Pillow's default (about 89 MP), so the
# limit is ours, documented and tested. A module attribute of Pillow, so it
# applies to every Image.open in the process, EXIF reading included.
Image.MAX_IMAGE_PIXELS = MAX_FRAME_PIXELS

MALFORMED_FILE_ERRORS: Final = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    Image.DecompressionBombWarning,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    ZeroDivisionError,
    SyntaxError,
    struct.error,
)
"""What Pillow and ``struct`` raise on a truncated, malformed or oversized file.

Explicit so a programming error still surfaces instead of being swallowed."""


def exceeded_limit(image: Image.Image) -> str | None:
    """Return which decoding limit ``image`` exceeds, if any.

    Only header fields are read (size and frame count), so nothing is decoded.

    Args:
        image: An image opened but not loaded.

    Returns:
        ``"frame_pixels"``, ``"frames"`` or ``"total_pixels"`` for the first
        limit exceeded, or ``None`` if the image may be decoded.
    """
    width, height = image.size
    frames = int(getattr(image, "n_frames", 1))
    if width * height > MAX_FRAME_PIXELS:
        return "frame_pixels"
    if frames > MAX_FRAMES:
        return "frames"
    if width * height * frames > MAX_TOTAL_PIXELS:
        return "total_pixels"
    return None
