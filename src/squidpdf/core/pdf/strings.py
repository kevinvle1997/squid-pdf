"""Strings found in bytes as a PDF writes them: in brackets or in hex, wherever one may start.

Pure: the bytes come as they are, and nothing here reads them through MuPDF, so a
reader that starts a string elsewhere than MuPDF does is still caught.
"""

from __future__ import annotations

import re
from collections.abc import Callable

# An escape in a string in brackets: octal digits, a line end, or one byte.
_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(\r\n|.))", re.DOTALL)
# What a string in brackets reads apart from its bytes: an escape, or a bracket.
_LITERAL_PART = re.compile(_ESCAPE.pattern + rb"|[()]", re.DOTALL)
_BRACKET_DEPTH = {b"(": 1, b")": -1}  # how each bracket changes how deep in strings it is
# What an escape stands for, by what follows the backslash, but octal digits; any other, itself.
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
# A string written in hex, from a < up to the > that ends it.
_HEX_STRING = re.compile(rb"<[^>]*")
# What a string written in hex reads past: anything but its digits.
_NOT_HEX = re.compile(rb"[^0-9A-Fa-f]")
# Printable ASCII and white space, no escape (\) or hex string (<): its strings read as written.
_PLAIN_DRAWING = re.compile(rb"[^\\<\x00-\x08\x0b\x0e-\x1f\x7f-\xff]*")


def may_hold_a_string(written: bytes) -> bool:
    """Whether `written` may hold a string: one starts at ( or at a < that isn't half of <<."""
    return b"(" in written or b"<" in written.replace(b"<<", b"")


def literal_strings_in(written: bytes) -> list[bytes]:
    """The bytes of each string in brackets in `written`, through the ) that closes it.

    One inside another is read only as part of it, since it ends inside it.
    """
    found: list[bytes] = []
    start = written.find(b"(")
    while start != -1:
        end = _literal_end(written, start)
        found.append(_ESCAPE.sub(_unescaped, written[start + 1 : end]))
        start = written.find(b"(", end)
    return found


def _literal_end(written: bytes, start: int) -> int:
    """Just past the ) that closes the string in brackets at `written[start]`, or the end."""
    depth = 0  # strings open: the one at `start`, and each in it
    for part in _LITERAL_PART.finditer(written, start):
        # .get: an escape changes no depth.
        depth += _BRACKET_DEPTH.get(part[0], 0)
        # Closed: back out of the one at `start`.
        if depth == 0:
            return part.end()
    return len(written)


def _unescaped(escape: re.Match[bytes]) -> bytes:
    """What an escape in a string in brackets stands for: a byte, or nothing for a line end."""
    octal, other = escape.groups()
    # Octal digits: the byte they number, past 255 wrapping, as MuPDF and pdf.js read it.
    if octal is not None:
        return bytes([int(octal, 8) % 256])
    return _ESCAPED.get(other, other)


def hex_strings_in(written: bytes) -> list[bytes]:
    """The bytes of each string in hex in `written`, whole and from each < to the next.

    Each from its first digit and its second too: a reader starting partway lines up with one.
    """
    found: list[bytes] = []
    for string in _HEX_STRING.finditer(written):
        whole = string[0]
        # From the first <, then from each alone: with one <, the two are one.
        parts = [whole, *whole.split(b"<")[1:]]
        for digits in dict.fromkeys(_NOT_HEX.sub(b"", part) for part in parts):
            found += [_bytes_of_hex(digits[skip:]) for skip in (0, 1)]
    return found


def _bytes_of_hex(digits: bytes) -> bytes:
    """The bytes hex digits write, a last digit alone read with a 0 after it, as readers do."""
    return bytes.fromhex((digits + b"0" * (len(digits) % 2)).decode())


def may_hold(drawing: bytes, holding: Callable[[str], bool]) -> bool:
    """Whether a string in `drawing` may hold a word `holding` looks for.

    A plain drawing `holding` is false of, read whole, holds none: a string's
    brackets set its words apart.
    """
    # Not plain: a string in it may read other than it's written.
    if _PLAIN_DRAWING.fullmatch(drawing) is None:
        return True
    return holding(drawing.decode("ascii"))
