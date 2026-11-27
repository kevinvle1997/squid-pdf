"""A reader that counts an image's bytes stops at its pixels, maybe before its bytes end."""

from __future__ import annotations

import base64
import zlib
from collections.abc import Callable
from dataclasses import dataclass

import squidpdf.core.constants as constants

_FLATE_STEP = 4096  # bytes fed to Flate's decoder at a time, before byte by byte
_A85_GROUP = 5
_WHITE_SPACE = b" \t\n\r\x00\x0c"
_A85_ZEROS = ord("z")
_A85_END = ord("~")
_HEX_END = ord(">")
_HEX_DIGITS = b"0123456789abcdefABCDEF"
_RUN_END = 128
_RUN_REPEATS = 257  # a length byte past 128 repeats the next byte 257 less it times
_MARKING_THEIR_END = frozenset(
    {"ASCII85Decode", "A85", "ASCIIHexDecode", "AHx", "DCTDecode", "DCT"}
)


@dataclass(frozen=True, slots=True)
class _Filter:
    fewest: Callable[[bytes, int], int | None]
    decoded: Callable[[bytes, int], bytes]


def fewest_giving(written: bytes, filters: list[str], wanted: int) -> int:
    """0 for a filter not sized here, or bytes that can't be read: fewer is the safe side."""
    if not filters:
        return wanted
    if not all(name in _FILTERS for name in filters):
        return 0
    steps = [_FILTERS[name] for name in filters]
    inputs = [written]
    at_most = len(written) * constants.DECODED_PER_BYTE
    try:
        for step in steps[:-1]:
            inputs.append(step.decoded(inputs[-1], at_most))
        for step, given in zip(reversed(steps), reversed(inputs), strict=True):
            fewest = step.fewest(given, wanted)
            if fewest is None:
                return 0
            wanted = fewest
    except zlib.error, ValueError:
        return 0
    return wanted


def marks_its_end(filters: list[str]) -> bool:
    return bool(filters) and filters[0] in _MARKING_THEIR_END


def _flate_fewest(written: bytes, wanted: int) -> int | None:
    decoder = zlib.decompressobj()
    given = 0
    for start in range(0, len(written), _FLATE_STEP):
        before = decoder.copy()
        step = len(decoder.decompress(written[start : start + _FLATE_STEP]))
        if given + step < wanted:
            given += step
            continue
        for taken, byte in enumerate(written[start : start + _FLATE_STEP], start=1):
            given += len(before.decompress(bytes([byte])))
            if given >= wanted:
                return start + taken
    return None


def _flate_decoded(written: bytes, at_most: int) -> bytes:
    return zlib.decompressobj().decompress(written, at_most)


def _a85_fewest(written: bytes, wanted: int) -> int | None:
    given = 0
    in_group = 0
    for taken, byte in enumerate(written, start=1):
        if byte == _A85_END:
            ends_here = in_group > 0 and given + in_group - 1 >= wanted
            return taken if ends_here else None
        if byte in _WHITE_SPACE:
            continue
        given, in_group = _a85_after(byte, given=given, in_group=in_group)
        if given >= wanted:
            return taken
    return None


def _a85_after(byte: int, *, given: int, in_group: int) -> tuple[int, int]:
    if byte == _A85_ZEROS and in_group == 0:
        return given + _A85_GROUP - 1, 0
    if in_group + 1 == _A85_GROUP:
        return given + _A85_GROUP - 1, 0
    # Any other character counts, as a reader that takes any into a group counts it.
    return given, in_group + 1


def _a85_decoded(written: bytes, at_most: int) -> bytes:
    before_end = written.split(b"~", 1)[0]
    return base64.a85decode(before_end, ignorechars=_WHITE_SPACE)[:at_most]


def _hex_fewest(written: bytes, wanted: int) -> int | None:
    digits = 0
    for taken, byte in enumerate(written, start=1):
        if byte == _HEX_END:
            return taken if -(-digits // 2) >= wanted else None
        digits += byte not in _WHITE_SPACE
        if digits // 2 >= wanted:
            return taken
    return None


def _hex_decoded(written: bytes, at_most: int) -> bytes:
    digits = bytes(byte for byte in written.split(b">", 1)[0] if byte in _HEX_DIGITS)
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())[:at_most]


def _run_length_fewest(written: bytes, wanted: int) -> int | None:
    given = 0
    at = 0
    while at < len(written) and written[at] != _RUN_END:
        length = written[at]
        as_they_are = length < _RUN_END
        run = length + 1
        if as_they_are and given + run >= wanted:
            return at + 1 + wanted - given
        if as_they_are:
            given += run
            at += 1 + run
            continue
        given += _RUN_REPEATS - length
        at += 2
        if given >= wanted:
            return min(at, len(written))
    return None


def _run_length_decoded(written: bytes, at_most: int) -> bytes:
    given = bytearray()
    at = 0
    while at < len(written) and written[at] != _RUN_END and len(given) < at_most:
        length = written[at]
        if length > _RUN_END:
            given += written[at + 1 : at + 2] * (_RUN_REPEATS - length)
            at += 2
            continue
        given += written[at + 1 : at + 2 + length]
        at += 2 + length
    return bytes(given[:at_most])


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
