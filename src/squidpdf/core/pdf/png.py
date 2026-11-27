"""MuPDF writes every PNG in colour; compressing three equal channels is most of the time."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from itertools import chain

import pymupdf
from zlib_ng import zlib_ng

import squidpdf.core.constants as constants

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_UNFILTERED = b"\x00"
_BIT_DEPTH = 8
_STANDARD_METHODS = (0, 0, 0)
_METER_UNIT = 1
_INCH_M = 0.0254


@dataclass(frozen=True, slots=True)
class _Layout:
    color_type: int
    channels: int


_GRAY = _Layout(color_type=0, channels=1)
_COLOR = _Layout(color_type=2, channels=3)


def png_of(drawn: pymupdf.Pixmap) -> bytes:
    if drawn.n != _COLOR.channels or drawn.alpha:
        raise ValueError(
            f"a PNG is written from 3 channels, no alpha; got {drawn.n}, {drawn.alpha}"
        )
    rgb = drawn.samples
    red = rgb[0::3]
    if red == rgb[1::3] == rgb[2::3]:
        return _png_from(red, drawn, layout=_GRAY, level=constants.PNG_GRAY_LEVEL)
    return _png_from(rgb, drawn, layout=_COLOR, level=constants.PNG_COLOR_LEVEL)


def _png_from(pixels: bytes, drawn: pymupdf.Pixmap, *, layout: _Layout, level: int) -> bytes:
    row_bytes = drawn.width * layout.channels
    rows = memoryview(pixels)
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
    checksum = zlib_ng.crc32(name + data)
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", checksum)


def _pixels_per_meter(per_inch: int) -> int:
    return round(per_inch / _INCH_M)
