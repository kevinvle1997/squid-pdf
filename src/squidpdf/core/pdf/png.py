"""A drawn page or box written as a PNG: in one grey channel when every pixel is grey.

A page of text is drawn in colour but is grey throughout, and compressing three equal
channels is most of a page image's time: MuPDF writes every PNG in colour, at one level.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from itertools import chain

import pymupdf
from zlib_ng import zlib_ng

import squidpdf.core.constants as constants

_SIGNATURE = b"\x89PNG\r\n\x1a\n"  # the bytes every PNG starts with
_UNFILTERED = b"\x00"  # a row's first byte when its bytes follow as they are
_BIT_DEPTH = 8  # bits per channel
_STANDARD_METHODS = (0, 0, 0)  # zlib, the one set of row filters, and no interlacing
_METER_UNIT = 1  # the unit of a resolution, as a PNG names the metre
_INCH_M = 0.0254  # metres in an inch
# Rows the grey check reads at a time: a colour page shows it in its first few blocks.
_GRAY_CHECK_ROWS = 64


@dataclass(frozen=True, slots=True)
class _Layout:
    """How a PNG lays out its pixels."""

    color_type: int  # as the PNG header names it
    channels: int  # bytes per pixel


_GRAY = _Layout(color_type=0, channels=1)
_COLOR = _Layout(color_type=2, channels=3)


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
    red = _red_if_gray(rgb, row_bytes=drawn.stride)
    # Every pixel grey, as on a page of text: red, the same as green and blue, holds all three.
    if red is not None:
        return _png_from(red, drawn, layout=_GRAY, level=constants.PNG_GRAY_LEVEL)
    return _png_from(rgb, drawn, layout=_COLOR, level=constants.PNG_COLOR_LEVEL)


def _red_if_gray(rgb: bytes, *, row_bytes: int) -> bytes | None:
    """The red of `rgb` if every pixel is grey; None from the first block of rows that isn't."""
    reds: list[bytes] = []
    block_bytes = row_bytes * _GRAY_CHECK_ROWS  # whole rows, so each block starts on a pixel
    for start in range(0, len(rgb), block_bytes):
        block = rgb[start : start + block_bytes]
        red = block[0::3]
        # A colour pixel in this block: no need to read the rest of the page.
        if not (red == block[1::3] == block[2::3]):
            return None
        reds.append(red)
    return b"".join(reds)


def _png_from(pixels: bytes, drawn: pymupdf.Pixmap, *, layout: _Layout, level: int) -> bytes:
    """`pixels`, in `drawn`'s rows, as a PNG in `layout`, compressed at zlib's `level`."""
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
        _chunk(b"IDAT", zlib_ng.compress(unfiltered, level)),
        _chunk(b"IEND", b""),
    ]
    return _SIGNATURE + b"".join(chunks)


def _chunk(name: bytes, data: bytes) -> bytes:
    """One chunk of a PNG: its length, its name, `data`, and a checksum of the last two."""
    checksum = zlib_ng.crc32(name + data)
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", checksum)


def _pixels_per_meter(per_inch: int) -> int:
    """A resolution in pixels per inch, as pixels per metre, the unit a PNG gives it in."""
    return round(per_inch / _INCH_M)
