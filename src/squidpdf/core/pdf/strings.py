"""Nothing here reads through MuPDF, so a reader that starts a string elsewhere is caught."""

from __future__ import annotations

import re
from collections.abc import Callable

_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(\r\n|.))", re.DOTALL)
_LITERAL_PART = re.compile(_ESCAPE.pattern + rb"|[()]", re.DOTALL)
_BRACKET_DEPTH = {b"(": 1, b")": -1}
_ESCAPED = {
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"b": b"\b",
    b"f": b"\f",
    b"\r\n": b"",
    b"\r": b"",
    b"\n": b"",
}
_HEX_STRING = re.compile(rb"<[^>]*")
_NOT_HEX = re.compile(rb"[^0-9A-Fa-f]")
_PLAIN_DRAWING = re.compile(rb"[^\\<\x00-\x08\x0b\x0e-\x1f\x7f-\xff]*")


def may_hold_a_string(written: bytes) -> bool:
    return b"(" in written or b"<" in written.replace(b"<<", b"")


def literal_strings_in(written: bytes) -> list[bytes]:
    found: list[bytes] = []
    start = written.find(b"(")
    while start != -1:
        end = _literal_end(written, start)
        found.append(_ESCAPE.sub(_unescaped, written[start + 1 : end]))
        start = written.find(b"(", end)
    return found


def _literal_end(written: bytes, start: int) -> int:
    depth = 0
    for part in _LITERAL_PART.finditer(written, start):
        depth += _BRACKET_DEPTH.get(part[0], 0)
        if depth == 0:
            return part.end()
    return len(written)


def _unescaped(escape: re.Match[bytes]) -> bytes:
    octal, other = escape.groups()
    # Past 255 wrapping, as MuPDF and pdf.js read it.
    if octal is not None:
        return bytes([int(octal, 8) % 256])
    return _ESCAPED.get(other, other)


def hex_strings_in(written: bytes) -> list[bytes]:
    found: list[bytes] = []
    for string in _HEX_STRING.finditer(written):
        whole = string[0]
        parts = [whole, *whole.split(b"<")[1:]]
        for digits in dict.fromkeys(_NOT_HEX.sub(b"", part) for part in parts):
            found += [_bytes_of_hex(digits[skip:]) for skip in (0, 1)]
    return found


def _bytes_of_hex(digits: bytes) -> bytes:
    """A last digit alone is read with a 0 after it, as readers do."""
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())


def may_hold(drawing: bytes, holding: Callable[[str], bool]) -> bool:
    if _PLAIN_DRAWING.fullmatch(drawing) is None:
        return True
    return holding(drawing.decode("ascii"))
