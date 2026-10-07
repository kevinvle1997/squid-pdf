"""How few of an image's bytes, written under its filters, give its pixels.

A reader that counts an image's bytes stops once it has its pixels, which may be
before its written bytes end. Pure: the filters come by name, and only Python's own
decoders read them.
"""

from __future__ import annotations

import base64
import zlib
from collections.abc import Callable
from dataclasses import dataclass

_FLATE_STEP = 4096  # bytes fed to Flate's decoder at a time, before byte by byte
_A85_GROUP = 5  # characters in a group of ASCII85, which give a byte fewer
_A85_LETTERS = range(ord("!"), ord("u") + 1)  # what an ASCII85 group is written in
_A85_ZEROS = ord("z")  # one character for a group of four zero bytes
_A85_END = ord("~")  # where ASCII85 ends, written ~>
_HEX_END = ord(">")  # where ASCIIHex ends
_HEX_DIGITS = b"0123456789abcdefABCDEF"
_RUN_END = 128  # the length byte that ends RunLength
_RUN_REPEATS = 257  # a length byte past 128 repeats the next byte this less it times
# Filters whose bytes mark their own end, which a reader that doesn't count them looks for.
_MARKING_THEIR_END = frozenset(
    {"ASCII85Decode", "A85", "ASCIIHexDecode", "AHx", "DCTDecode", "DCT"}
)


@dataclass(frozen=True, slots=True)
class _Filter:
    """How a filter's bytes read back: how few give so many, and what all of them give."""

    fewest: Callable[[bytes, int], int | None]  # None: they never give so many
    decoded: Callable[[bytes], bytes]


def fewest_giving(written: bytes, filters: list[str], wanted: int) -> int:
    """How few of `written` give `wanted` bytes through `filters`, in the order they're undone.

    0 when a filter isn't one sized here, or its bytes can't be read: fewer is the safe side.
    """
    # No filter: its bytes are its pixels.
    if not filters:
        return wanted
    # A filter not sized here, as LZW or DCT: any of its bytes may be the last.
    if not all(name in _FILTERS for name in filters):
        return 0
    steps = [_FILTERS[name] for name in filters]
    inputs = [written]  # each filter's bytes: what the ones undone before it give
    # zlib, and base64's ASCII85, raise on bytes they can't read.
    try:
        for step in steps[:-1]:
            inputs.append(step.decoded(inputs[-1]))
        for step, given in zip(reversed(steps), reversed(inputs), strict=True):
            fewest = step.fewest(given, wanted)
            # They never give so many: nothing says where a reader stops.
            if fewest is None:
                return 0
            wanted = fewest
    except zlib.error, ValueError:
        return 0
    return wanted


def marks_its_end(filters: list[str]) -> bool:
    """Whether an image's bytes end themselves, as its first filter writes them."""
    return bool(filters) and filters[0] in _MARKING_THEIR_END


def _flate_fewest(written: bytes, wanted: int) -> int | None:
    """How few of Flate's bytes give `wanted` bytes."""
    decoder = zlib.decompressobj()
    given = 0  # bytes decompressed so far
    for start in range(0, len(written), _FLATE_STEP):
        before = decoder.copy()  # to feed this step again byte by byte
        step = len(decoder.decompress(written[start : start + _FLATE_STEP]))
        # Not there yet within this step.
        if given + step < wanted:
            given += step
            continue
        for taken, byte in enumerate(written[start : start + _FLATE_STEP], start=1):
            given += len(before.decompress(bytes([byte])))
            # Enough: they end with this byte.
            if given >= wanted:
                return start + taken
    return None


def _flate_decoded(written: bytes) -> bytes:
    """What Flate's bytes give, as far as they can be read."""
    return zlib.decompressobj().decompress(written)


def _a85_fewest(written: bytes, wanted: int) -> int | None:
    """How few of ASCII85's characters give `wanted` bytes."""
    given = 0  # bytes given by whole groups so far
    in_group = 0  # characters of the group being read
    for taken, byte in enumerate(written, start=1):
        # Its end: a group left short gives a byte fewer than its characters.
        if byte == _A85_END:
            ends_here = in_group > 0 and given + in_group - 1 >= wanted
            return taken if ends_here else None
        # A group of zeros, in one character.
        if byte == _A85_ZEROS and in_group == 0:
            given += _A85_GROUP - 1
        # One character of a group.
        if byte in _A85_LETTERS:
            in_group += 1
        # A group is whole.
        if in_group == _A85_GROUP:
            in_group = 0
            given += _A85_GROUP - 1
        # Enough: they end with this character.
        if given >= wanted:
            return taken
    return None


def _a85_decoded(written: bytes) -> bytes:
    """What ASCII85's characters give, to its end."""
    before_end = written.split(b"~", 1)[0]
    return base64.a85decode(before_end, ignorechars=b" \t\n\r\x00\x0c")


def _hex_fewest(written: bytes, wanted: int) -> int | None:
    """How few of ASCIIHex's characters give `wanted` bytes."""
    digits = 0
    for taken, byte in enumerate(written, start=1):
        # Its end: a digit left alone gives a byte too.
        if byte == _HEX_END:
            return taken if -(-digits // 2) >= wanted else None
        digits += byte in _HEX_DIGITS
        # Enough: they end with this digit.
        if digits // 2 >= wanted:
            return taken
    return None


def _hex_decoded(written: bytes) -> bytes:
    """What ASCIIHex's characters give, to its end, a digit left alone read with a 0 after."""
    digits = bytes(byte for byte in written.split(b">", 1)[0] if byte in _HEX_DIGITS)
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())


def _run_length_fewest(written: bytes, wanted: int) -> int | None:
    """How few of RunLength's bytes give `wanted` bytes."""
    given = 0
    at = 0  # where the next run's length byte is
    while at < len(written) and written[at] != _RUN_END:
        length = written[at]
        # A run of the next byte, repeated.
        if length > _RUN_END:
            given += _RUN_REPEATS - length
            at += 2
        # A run of the next bytes as they are: enough may come partway in it.
        if length < _RUN_END:
            run = length + 1
            # Enough within this run: they end with its byte that gives the last.
            if given + run >= wanted:
                return at + 1 + wanted - given
            given += run
            at += 1 + run
        # Enough: they end with this run.
        if given >= wanted:
            return min(at, len(written))
    return None


def _run_length_decoded(written: bytes) -> bytes:
    """What RunLength's bytes give, to its end."""
    given = bytearray()
    at = 0
    while at < len(written) and written[at] != _RUN_END:
        length = written[at]
        # A run of the next byte, repeated.
        if length > _RUN_END:
            given += written[at + 1 : at + 2] * (_RUN_REPEATS - length)
            at += 2
            continue
        given += written[at + 1 : at + 2 + length]
        at += 2 + length
    return bytes(given)


# Each filter sized here, by its name in full and as an inline image writes it.
_FLATE = _Filter(fewest=_flate_fewest, decoded=_flate_decoded)
_A85 = _Filter(fewest=_a85_fewest, decoded=_a85_decoded)
_HEX = _Filter(fewest=_hex_fewest, decoded=_hex_decoded)
_RUN_LENGTH = _Filter(fewest=_run_length_fewest, decoded=_run_length_decoded)
_FILTERS = {
    "FlateDecode": _FLATE,
    "Fl": _FLATE,
    "ASCII85Decode": _A85,
    "A85": _A85,
    "ASCIIHexDecode": _HEX,
    "AHx": _HEX,
    "RunLengthDecode": _RUN_LENGTH,
    "RL": _RUN_LENGTH,
}
