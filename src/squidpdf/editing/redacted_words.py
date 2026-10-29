"""The words a redaction of some of a span takes: checked, widened, merged, found on the page.

A range counts code points, as the browser sends it; a run is the words of one range, as a
span of its own. A redaction of all of a span takes its whole text's range.
"""

from __future__ import annotations

from collections.abc import Sequence

from squidpdf.core import (
    Engine,
    PlacedLetter,
    Rect,
    RedactedRun,
    Span,
    run_of,
    whole_run,
    words_around,
    words_beside,
)
from squidpdf.editing.edits import Redact
from squidpdf.editing.errors import BadRange
from squidpdf.editing.types import Redacted


def range_of(redact: Redact, span: Span) -> range:
    """The code points of `span`'s text `redact` takes, whole words: its range, or all of it.

    Raises BadRange for a range the text doesn't have, or one with only spaces in it.
    """
    if redact.range is None:
        return range(len(span.text))
    start, end = redact.range
    in_text = 0 <= start < end <= len(span.text)
    widened = words_around(span.text, range(start, end)) if in_text else None
    # Not in its text, or nothing but spaces: skipped, it would be a leak.
    if widened is None:
        debug = f"range [{start}, {end}) of a text {len(span.text)} long"
        raise BadRange(redact.span_id, debug=debug)
    return widened


def merged_ranges(ranges: Sequence[range]) -> tuple[range, ...]:
    """`ranges` in the text's order, those that overlap or meet as one."""
    merged: list[range] = []
    for asked in sorted(ranges, key=lambda asked: asked.start):
        meets_last = bool(merged) and asked.start <= merged[-1].stop
        if meets_last:
            merged[-1] = range(merged[-1].start, max(merged[-1].stop, asked.stop))
            continue
        merged.append(asked)
    return tuple(merged)


def redacted_of(engine: Engine, span: Span, ranges: tuple[range, ...]) -> Redacted:
    """What redacting the words `ranges` name of `span` takes and leaves, among its letters.

    Ask before the erase: it reads the page.
    """
    # All of it: nothing left on the line.
    if ranges == (range(len(span.text)),):
        return Redacted(span, (whole_run(span),), ranges, rest=())
    drawn = [
        letter for letter in engine.redaction.letters_of(span) if not letter.letter.isspace()
    ]
    found = [_drawn_for(span.text, asked, drawn=drawn) for asked in ranges]
    places = [place for place in found if place is not None]
    # Not among its letters, as when the text read isn't what's drawn: the whole line goes,
    # and its hidden copies lose only the words.
    if len(places) < len(found):
        whole_line = tuple(_run_of(span, asked, box=span.bbox) for asked in ranges)
        return Redacted(span, whole_line, ranges, rest=None)
    boxes = [_box_around(drawn[place.start : place.stop]) for place in places]
    in_words = {at for place in places for at in place}
    spelled = {drawn[at].letter for at in in_words}
    # A copy of the words' letters drawn over them, as for a bold or a shadow, goes with them.
    copies = {
        at
        for at, letter in enumerate(drawn)
        if letter.letter in spelled and any(_middle_in(letter, box) for box in boxes)
    }
    return Redacted(
        span,
        tuple(_run_of(span, asked, box=box) for asked, box in zip(ranges, boxes, strict=True)),
        ranges,
        rest=tuple(letter for at, letter in enumerate(drawn) if at not in in_words | copies),
    )


def _drawn_for(text: str, asked: range, *, drawn: list[PlacedLetter]) -> range | None:
    """Where in `drawn` the letters for `asked` of `text` are: the same occurrence of its words.

    Spaces aside, and by the letters, not their places: text a marked content gives in place
    of its glyphs can be in another order. None where they aren't drawn as often.
    """
    words = "".join(text[asked.start : asked.stop].split())
    before = "".join(text[: asked.start].split())
    occurrence = len(_starts("".join(text.split()), words, before=len(before)))
    starts = _starts("".join(letter.letter for letter in drawn), words)
    # Drawn fewer times than its text has them before this one.
    if occurrence >= len(starts):
        return None
    at = starts[occurrence]
    return range(at, at + len(words))


def _starts(text: str, words: str, *, before: int | None = None) -> list[int]:
    """Where in `text` each occurrence of `words` starts, overlapping or not; those `before`."""
    starts: list[int] = []
    at = text.find(words)
    while at >= 0 and (before is None or at < before):
        starts.append(at)
        at = text.find(words, at + 1)
    return starts


def _middle_in(letter: PlacedLetter, box: Rect) -> bool:
    """Whether `letter`'s middle is inside `box`, as an erase takes a letter."""
    middle_x = (letter.box.x0 + letter.box.x1) / 2
    middle_y = (letter.box.y0 + letter.box.y1) / 2
    return box.x0 <= middle_x <= box.x1 and box.y0 <= middle_y <= box.y1


def _box_around(letters: Sequence[PlacedLetter]) -> Rect:
    """The smallest box holding each of `letters`' boxes."""
    box = letters[0].box
    for letter in letters[1:]:
        box = box.union(letter.box)
    return box


def _run_of(span: Span, asked: range, *, box: Rect) -> RedactedRun:
    """The words `asked` names of `span` as a run of their own, in `box`, beside its others."""
    before, after = words_beside(span.text, asked)
    text = span.text[asked.start : asked.stop]
    return run_of(span, text=text, box=box, before=before, after=after)


def runs_of(redacted: Sequence[Redacted]) -> list[RedactedRun]:
    """Every run of words the redactions take, each a span of its own, in order."""
    return [run for each in redacted for run in each.runs]


def runs_text(redacted: Redacted) -> str:
    """The words a span's redactions take, as a sentence quotes them, an ellipsis between."""
    return " … ".join(run.text.strip() for run in redacted.runs)
