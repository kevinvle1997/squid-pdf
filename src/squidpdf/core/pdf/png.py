"""A drawn page or box written as a PNG: in one grey channel when every pixel is grey.

A page of text is drawn in colour but is grey throughout, and compressing three equal
channels is most of a page image's time: MuPDF writes every PNG in colour, at one level.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from itertools import chain

import pymupdf

from squidpdf.core.constants import PNG_COLOR_LEVEL, PNG_GRAY_LEVEL

_SIGNATURE = b"\x89PNG\r\n\x1a\n"  # the bytes every PNG starts with
_UNFILTERED = b"\x00"  # a row's first byte when its bytes follow as they are
_BIT_DEPTH = 8  # bits per channel
_STANDARD_METHODS = (0, 0, 0)  # zlib, the one set of row filters, and no interlacing
_METER_UNIT = 1  # the unit of a resolution, as a PNG names the metre
_INCH_M = 0.0254  # metres in an inch


@dataclass(frozen=True, slots=True)
class _Layout:
    """How a PNG lays out its pixels, and how hard zlib works on them."""

    color_type: int  # as the PNG header names it
    channels: int  # bytes per pixel
    level: int  # zlib's


_GRAY = _Layout(color_type=0, channels=1, level=PNG_GRAY_LEVEL)
_COLOR = _Layout(color_type=2, channels=3, level=PNG_COLOR_LEVEL)


def png_of(drawn: pymupdf.Pixmap) -> bytes:
    """`drawn` as a PNG at its resolution: in one grey channel when every pixel is grey.

    Raises ValueError for a pixmap in anything but colour without alpha, which nothing draws.
    """
    # Its bytes are read three to a pixel: any other layout would be written as noise.
    if drawn.n != _COLOR.channels or drawn.alpha:
        raise ValueError(
            f"a PNG is written from 3 channels, no alpha; got {drawn.n}, {drawn.alpha}"
        )
    rgb = drawn.samples
    red = rgb[0::3]
    # Every pixel grey, as on a page of text: red, the same as green and blue, holds all three.
    if red == rgb[1::3] == rgb[2::3]:
        return _png_from(red, drawn, layout=_GRAY)
    return _png_from(rgb, drawn, layout=_COLOR)


def _png_from(pixels: bytes, drawn: pymupdf.Pixmap, *, layout: _Layout) -> bytes:
    """`pixels`, in `drawn`'s rows, as a PNG in `layout` at `drawn`'s size and resolution."""
    row_bytes = drawn.width * layout.channels
    rows = memoryview(pixels)  # sliced without a copy: the join copies each row once
    unfiltered = b"".join(
        chain.from_iterable(
            (_UNFILTERED, rows[start : start + row_bytes])
            for start in range(0, len(pixels), row_bytes)
        )
    )
    header = struct.pack(
        ">IIBB3B", drawn.width, drawn.height, _BIT_DEPTH, layout.color_type, *_STANDARD_METHODS
    )
    resolution = struct.pack(
        ">IIB", _pixels_per_meter(drawn.xres), _pixels_per_meter(drawn.yres), _METER_UNIT
    )
    chunks = [
        _chunk(b"IHDR", header),
        _chunk(b"pHYs", resolution),
        _chunk(b"IDAT", zlib.compress(unfiltered, layout.level)),
        _chunk(b"IEND", b""),
    ]
    return _SIGNATURE + b"".join(chunks)


def _chunk(name: bytes, data: bytes) -> bytes:
    """One chunk of a PNG: its length, its name, `data`, and a checksum of the last two."""
    return (
        struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    )


def _pixels_per_meter(per_inch: int) -> int:
    """A resolution in pixels per inch, as pixels per metre, the unit a PNG gives it in."""
    return round(per_inch / _INCH_M)
