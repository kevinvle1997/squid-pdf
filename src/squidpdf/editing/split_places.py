"""Each part of a split span is a span of its own, so a part no edit covers stays put."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from squidpdf.core import Engine, PlacedLetter, Rect, Span


def part_of(engine: Engine, span: Span, covered: range) -> Span | None:
    """The code points `covered` of `span`'s text as a span of their own, where drawn.

    None where its letters can't be told apart. Ask before the erase: it reads the page.
    """
    # All of it: the span as it is, with nothing to find.
    if covered == range(len(span.text)):
        return span
    letters = engine.redaction.letters_of(span)
    start_x = x_at(letters, span, covered.start)
    end_x = x_at(letters, span, covered.stop)
    if start_x is None or end_x is None:
        return None
    _x, baseline = span.origin
    box = Rect(start_x, span.bbox.y0, end_x, span.bbox.y1)
    text = span.text[covered.start : covered.stop]
    return replace(span, text=text, origin=(start_x, baseline), bbox=box, fragments=())


def x_at(letters: Sequence[PlacedLetter], span: Span, at: int) -> float | None:
    """Where code point `at` of `span`'s text starts on the page; None where it can't be told.

    A space before it ends where the letter before it ends, since a page may draw no space.
    """
    if at == 0:
        start_x, _baseline = span.origin
        return start_x
    if at == len(span.text):
        return span.bbox.x1
    ink = [letter for letter in letters if not letter.letter.isspace()]
    # A ligature, or text a marked content gives, spells it otherwise: no letter to split at.
    if "".join(letter.letter for letter in ink) != "".join(span.text.split()):
        return None
    before = len("".join(span.text[:at].split()))
    if not span.text[at].isspace():
        return ink[before].origin[0]
    start_x, _baseline = span.origin
    return ink[before - 1].box.x1 if before else start_x


def ink_after(engine: Engine, span: Span, end: int) -> float | None:
    """Where the first letter of `span` at or after code point `end` starts, spaces aside.

    None where only spaces follow, or where its letters can't be told apart.
    """
    rest = span.text[end:]
    if not rest.strip():
        return None
    first_ink = end + len(rest) - len(rest.lstrip())
    return x_at(engine.redaction.letters_of(span), span, first_ink)


def uncovered(length: int, covered: Sequence[range]) -> list[range]:
    """The parts of a text `length` code points long that none of `covered` takes, in order."""
    left: list[range] = []
    at = 0
    for taken in sorted(covered, key=lambda taken: taken.start):
        if taken.start > at:
            left.append(range(at, taken.start))
        at = max(at, taken.stop)
    if at < length:
        left.append(range(at, length))
    return left
