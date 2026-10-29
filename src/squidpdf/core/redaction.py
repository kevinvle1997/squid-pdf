"""Whether a redaction's words are gone: from each span's box, and from every hidden copy.

Read-only checks, written against the driver's primitives, asked as
`engine.redaction` before an edit (where else the words hide) and after it
(whether any are left). The engine's erase matches words as these checks do.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from itertools import pairwise, product

from squidpdf.core.constants import MOVED_PT, ROUNDED_PT, SIZE_EPS
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.types import (
    COPY_PLACES,
    QUARTER_TURNS,
    CopyPlace,
    DrawnElsewhere,
    DrawnGlyph,
    DrawnLine,
    HiddenCopy,
    HiddenPlace,
    Page,
    PlacedLetter,
    Rect,
    RedactedRun,
    RestOfLine,
    Span,
    asked_by_page,
    by_page,
)

# A word: a run of letters and digits (`\w` without its underscore).
_WORD = re.compile(r"[^\W_]+")
# A box around everything a page can draw: to read all of its letters.
_EVERYWHERE = Rect(-math.inf, -math.inf, math.inf, math.inf)


@dataclass(frozen=True, slots=True, eq=False)
class RedactionCheck:
    """Where a redacted span's words hide, and whether any are left. Made by `open_engine`."""

    driver: PdfDriver

    def hidden_places(self, runs: Iterable[RedactedRun]) -> dict[str, list[HiddenPlace]]:
        """Where else each run's words have a hidden copy, by its span's id.

        Ask before `Engine.drop_hidden_copies` takes them out. A signed document names its
        signatures for every span: any redaction takes them.
        """
        asked = list(runs)
        # No span: the document's own copies needn't be read.
        if not asked:
            return {}
        holding = partial(holds, whole_words(asked))
        document_copies = self.driver.document_hidden_copies(holding)
        places: dict[str, list[CopyPlace]] = {}
        for page, on_page in by_page(asked).items():
            places |= self._places_on(page, on_page, document_copies=document_copies)
        signed: list[HiddenPlace] = ["signatures"] if self.driver.is_signed() else []
        return {span_id: [*held, *signed] for span_id, held in places.items()}

    def _places_on(
        self, page: int, runs: list[RedactedRun], *, document_copies: list[HiddenCopy]
    ) -> dict[str, list[CopyPlace]]:
        """Where the words of `runs`, all on `page`, have a hidden copy: there, or the file's.

        By span id: the runs of words a redaction takes of one span are that span's words.
        """
        on_page = [
            HiddenCopy("screen_reader_text", text) for text in self.driver.hidden_copies(page)
        ]
        copies = on_page + document_copies
        by_id: dict[str, list[RedactedRun]] = {}
        for run in runs:
            by_id.setdefault(run.id, []).append(run)
        return {
            span_id: _places_holding(whole_words(of_span), copies)
            for span_id, of_span in by_id.items()
        }

    def letters_of(self, span: Span) -> list[PlacedLetter]:
        """The letters the page draws for `span`: in its box, at its size, in reading order.

        Other text drawn in its box at another size isn't its own.
        """
        [letters] = self.driver.letters_in(span.page, [span.bbox])
        return [letter for letter in letters if abs(letter.size - span.size) <= SIZE_EPS]

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """The spans with any word of their text still in their box. A black box won't hide it.

        Part of a card number is as much a leak as all of it. Only each span's
        own box is read, so the same words elsewhere aren't a leak. Spaces are
        ignored, so respacing can't hide a leftover.
        """
        return asked_by_page(spans, self._left_on)

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        """Those of `spans`, all on `page`, with any word of their text still in their box."""
        texts = self.driver.text_in(page, [span.bbox.grown(ROUNDED_PT) for span in spans])
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def kept_by(self, page: int, erased: Sequence[Rect]) -> list[DrawnGlyph]:
        """The glyphs the page draws that an erase of `erased` boxes leaves, spaces aside.

        Ask before the erase. It takes the glyph of each letter whose middle is in a box.
        """
        reached = [box.grown(ROUNDED_PT) for box in erased]
        taken = [letter for box in self.driver.letters_in(page, reached) for letter in box]
        return _less(_letters_of(self.driver.drawn_glyphs(page)), taken, near=_same_place)

    def drawn_elsewhere(
        self,
        page: int,
        kept: Sequence[DrawnGlyph],
        *,
        lines: Sequence[DrawnLine],
        rests: Sequence[Span] = (),
        words: Sequence[str] = (),
    ) -> DrawnElsewhere:
        """The glyphs drawn now, spaces aside, the erase was to take, or moved.

        Every glyph is read, on or off the page. None is one `kept` where it was, one of `lines`
        the edits drew, or the rest of the line of one of `rests`, set along it by one shift
        onto nothing else. Some of a line drawn before, in order, is moved; any other is taken.
        """
        drawn = self.driver.drawn_glyphs(page)
        now = _letters_of(drawn)
        gone = _less(list(kept), now, near=_same_place)
        new = _less(now, kept, near=_same_place)
        went = [[glyph for glyph in gone if _on_line_from(glyph, span)] for span in rests]
        # Each rest, before: what stayed where it was and what went, on the line, in order.
        before = [[glyph for glyph in kept if _on_line_from(glyph, span)] for span in rests]
        on_lines = [glyph for glyph in gone if any(_holds(each, glyph) for each in went)]
        rest_lines = _runs(on_lines, among=kept)
        runs = _runs(_not_on(new, lines), among=now)
        # Spelling words the erase takes, a run is those words, whatever other letters went.
        # The page's reading, for where it puts spaces: read only if a glyph is out of place.
        reading = self.driver.letters_in(page, [_EVERYWHERE])[0] if runs else []
        spelling = [run for run in runs if _spells_any(_spelled(run, reading), words)]
        others = [run for run in runs if not any(run is each for each in spelling)]
        along = [
            (run, matched)
            for run in others
            if (matched := _set_along(run, rest_lines)) is not None
        ]
        lined = [
            (run, matched)
            for run, matched in along
            if _one_line(matched, along, went=went, before=before)
        ]
        # Read only where some rest moved: a box for each glyph costs a reading of its own.
        box_of = _by_glyph(drawn, self.driver.drawn_glyph_boxes(page)) if lined else {}
        # None read, or out of step with the glyphs: where the rest landed can't be told.
        unchecked = box_of is None
        box_now = box_of or {}
        # Before the erase, as far as now tells: each rest's glyphs set back by its shift.
        box_then = box_now | {
            id(glyph): _set_off(box_now[id(glyph)], _offset(run[0], matched[0]))
            for run, matched in ([] if unchecked else lined)
            for glyph in run
        }
        # Unchecked, a rest is still the rest, not other text: said so, and refused for it.
        rest = [
            (run, matched)
            for run, matched in lined
            if unchecked
            or not _lands_on(run, matched, lined=lined, now=now, boxes=(box_now, box_then))
        ]
        off = [run for run in others if not any(run is each for each, _matched in rest)]
        gone_lines = _runs(gone, among=kept)
        moved = [run for run in off if _part_of(run, gone_lines) is not None]
        taken = spelling + [run for run in off if not any(run is each for each in moved)]
        size = self.driver.pages()[page]
        found = [glyph for _run, matched in rest for glyph in matched]
        # Set off the page, a glyph of the rest is gone, as far as a reader sees.
        shown = [
            was
            for run, matched in rest
            for glyph, was in zip(run, matched, strict=True)
            if _on_page(glyph, size)
        ]
        return DrawnElsewhere(
            [glyph for run in taken for glyph in run],
            [glyph for run in moved for glyph in run],
            rests=[_rest_now(each, found=found, shown=shown) for each in went],
            unchecked=unchecked,
        )

    def still_hidden(self, runs: Iterable[RedactedRun]) -> list[RedactedRun]:
        """The runs with a word of theirs still in a hidden copy on their page.

        Matched as `Engine.drop_hidden_copies` deletes them (`whole_words`).
        """
        return asked_by_page(runs, self._hidden_on)

    def _hidden_on(self, page: int, runs: list[RedactedRun]) -> list[RedactedRun]:
        """Those of `runs`, all on `page`, with a word of theirs in a hidden copy there."""
        hidden_copies = self.driver.hidden_copies(page)
        return [run for run in runs if _any_holds(whole_words([run]), hidden_copies)]

    def still_hidden_in_document(self, runs: Iterable[RedactedRun]) -> list[RedactedRun]:
        """The runs with a word of theirs still in one of the document's own hidden copies.

        Read as `Engine.drop_hidden_copies` deletes them, whichever pages were kept:
        none sits on one.
        """
        checked = list(runs)
        # No span, as an export with no redaction: the document's own needn't be read.
        if not checked:
            return []
        document_copies = self.driver.document_hidden_copies(
            partial(holds, whole_words(checked))
        )
        return [run for run in checked if _places_holding(whole_words([run]), document_copies)]


def words_around(text: str, asked: range) -> range | None:
    """`asked` of `text` widened to whole words, less spaces at its ends; None if all spaces.

    What a redaction of some words of a span takes. The browser widens a selection the same way:
    `tests/editing/redacted_words_cases.json` holds both to it.
    """
    start, end = asked.start, asked.stop
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    # Nothing but spaces: no word to take.
    if start == end:
        return None
    while start > 0 and _is_word_letter(text[start - 1]) and _is_word_letter(text[start]):
        start -= 1
    while end < len(text) and _is_word_letter(text[end - 1]) and _is_word_letter(text[end]):
        end += 1
    return range(start, end)


def _is_word_letter(letter: str) -> bool:
    """Whether `letter` is part of a word: a letter, a digit or a mark on one (L, N or M).

    Not `_WORD`, which leaves marks out: a selection widens as the browser's does, by category,
    while a hidden copy, composed already (NFKC), is read as `re` reads a word.
    """
    return unicodedata.category(letter)[0] in "LNM"


def any_word_left(text: str, left: str) -> bool:
    """Whether any word of `text`, or all of it, is in `left`, spaces aside.

    A one-letter word counts only within the whole text: redaction labels often hold "A".
    So a span of one letter is still looked for, as its whole text.
    """
    leftover = "".join(left.split())
    words = [word for word in text.split() if len(word) > 1]
    return any(word in leftover for word in [*words, "".join(text.split())])


def _not_on(glyphs: list[DrawnGlyph], lines: Sequence[DrawnLine]) -> list[DrawnGlyph]:
    """Of `glyphs`, those none of `lines` has: each of its letters once, on its baseline."""
    spare = [Counter("".join(line.text.split())) for line in lines]
    left: list[DrawnGlyph] = []
    for glyph in glyphs:
        drawing = [
            at
            for at, line in enumerate(lines)
            if spare[at][glyph.letter] > 0 and _on_line(glyph, line)
        ]
        if not drawing:
            left.append(glyph)
            continue
        spare[drawing[0]][glyph.letter] -= 1
    return left


def _on_line(glyph: DrawnGlyph, line: DrawnLine) -> bool:
    """Whether `glyph` starts on `line`'s baseline, from its start on, as the line runs."""
    cos, sin = QUARTER_TURNS[line.turn_ccw]
    # The page's y grows downward.
    direction = (cos, -sin)
    offset = (glyph.origin[0] - line.origin[0], glyph.origin[1] - line.origin[1])
    along = offset[0] * direction[0] + offset[1] * direction[1]
    return abs(_across(direction, offset)) <= MOVED_PT and along >= -MOVED_PT


def _letters_of(glyphs: list[DrawnGlyph]) -> list[DrawnGlyph]:
    """Every one of `glyphs` but a space."""
    return [glyph for glyph in glyphs if not glyph.letter.isspace()]


def _less[Glyph: (DrawnGlyph, PlacedLetter), Other: (DrawnGlyph, PlacedLetter)](
    glyphs: list[Glyph],
    others: Sequence[Other],
    *,
    near: Callable[[Other, Glyph], bool],
) -> list[Glyph]:
    """`glyphs` less one for each of `others`: of its letter, `near` where it is."""
    spares: dict[str, list[Other]] = {}
    for other in others:
        spares.setdefault(other.letter, []).append(other)
    left: list[Glyph] = []
    for glyph in glyphs:
        # .get: a letter none of `others` has.
        spare = spares.get(glyph.letter, [])
        found = [at for at, other in enumerate(spare) if near(other, glyph)]
        if not found:
            left.append(glyph)
            continue
        spare.pop(found[0])
    return left


def _same_place(was: DrawnGlyph | PlacedLetter, now: DrawnGlyph | PlacedLetter) -> bool:
    """Whether `now` starts where `was` did, give or take a rewrite's rounding."""
    return math.dist(was.origin, now.origin) <= MOVED_PT


def _runs(glyphs: list[DrawnGlyph], *, among: Sequence[DrawnGlyph]) -> list[list[DrawnGlyph]]:
    """`glyphs`, some of `among` in its order, split into the runs drawn together on a line.

    Split where `among` has another glyph between, or where the line changes.
    """
    runs: list[list[DrawnGlyph]] = []
    at = -1
    for glyph in glyphs:
        before = at
        at = next(place for place in range(at + 1, len(among)) if among[place] is glyph)
        joins = bool(runs) and before == at - 1 and _on_line_of(runs[-1][-1], glyph)
        if joins:
            runs[-1].append(glyph)
            continue
        runs.append([glyph])
    return runs


def _on_line_of(glyph: DrawnGlyph, other: DrawnGlyph) -> bool:
    """Whether `other` starts on `glyph`'s line, read the same way."""
    offset = (other.origin[0] - glyph.origin[0], other.origin[1] - glyph.origin[1])
    same_way = math.dist(glyph.direction, other.direction) <= _SAME_WAY
    return same_way and abs(_across(glyph.direction, offset)) <= MOVED_PT


# Two lines whose directions differ less than this read the same way.
_SAME_WAY = 0.01


def _part_of(run: list[DrawnGlyph], lines: list[list[DrawnGlyph]]) -> list[DrawnGlyph] | None:
    """The glyphs of one of `lines` that `run` is, in their order, wherever each was set.

    None if it's none. They're taken from their line, for no other run to be.
    """
    for line in lines:
        for start, first in enumerate(line):
            if first.letter != run[0].letter:
                continue
            matched = _matched_after(run, line, start=start)
            if matched is not None:
                return _taken_out(line, matched)
    return None


def _set_along(run: list[DrawnGlyph], lines: list[list[DrawnGlyph]]) -> list[DrawnGlyph] | None:
    """The glyphs of one of `lines` that `run` is, in their order, set along their line alike.

    None if it's none. They're taken from their line, for no other run to be.
    """
    for line in lines:
        for start, first in enumerate(line):
            shift = _offset(first, run[0])
            if first.letter != run[0].letter or abs(_across(first.direction, shift)) > MOVED_PT:
                continue
            matched = _matched_after(run, line, start=start, shift=shift)
            if matched is not None:
                return _taken_out(line, matched)
    return None


def _taken_out(line: list[DrawnGlyph], places: list[int]) -> list[DrawnGlyph]:
    """The glyphs at `places` in `line`, taken out of it."""
    was = [line[at] for at in places]
    for at in reversed(places):
        del line[at]
    return was


def _matched_after(
    run: list[DrawnGlyph],
    line: list[DrawnGlyph],
    *,
    start: int,
    shift: tuple[float, float] | None = None,
) -> list[int] | None:
    """Where in `line`, from `start` on, in order, each of `run` was: `shift` before, if any."""
    matched: list[int] = []
    at = start
    for glyph in run:
        found = next(
            (
                place
                for place in range(at, len(line))
                if line[place].letter == glyph.letter
                and (shift is None or math.dist(_offset(line[place], glyph), shift) <= MOVED_PT)
            ),
            None,
        )
        if found is None:
            return None
        matched.append(found)
        at = found + 1
    return matched


def _on_line_from(glyph: DrawnGlyph, span: Span) -> bool:
    """Whether `glyph` starts on `span`'s line: on its baseline, read the same way."""
    offset = (glyph.origin[0] - span.origin[0], glyph.origin[1] - span.origin[1])
    same_way = math.dist(glyph.direction, span.direction) <= _SAME_WAY
    return same_way and abs(_across(span.direction, offset)) <= MOVED_PT


def _spelled(run: list[DrawnGlyph], reading: list[PlacedLetter]) -> str:
    """`run` as the page's `reading` spells it, from its first glyph to its last.

    With a space wherever the reading puts one: at a gap kerned wide enough, as at a space
    drawn, since that's what the reader sees. A run the reading doesn't hold, off the page,
    is its letters alone.
    """
    first = _read_at(run[0], reading, start=0)
    last = None if first is None else _read_at(run[-1], reading, start=first)
    if first is None or last is None:
        return "".join(glyph.letter for glyph in run)
    return "".join(letter.letter for letter in reading[first : last + 1])


def _read_at(glyph: DrawnGlyph, reading: list[PlacedLetter], *, start: int) -> int | None:
    """Where in `reading`, from `start` on, `glyph` is read: its letter, where it's drawn."""
    return next(
        (
            at
            for at in range(start, len(reading))
            if reading[at].letter == glyph.letter and _same_place(reading[at], glyph)
        ),
        None,
    )


def _spells_any(spelled: str, words: Sequence[str]) -> bool:
    """Whether `spelled` holds a word of any of `words`, or all of one, whole.

    Whole: "Ann" isn't in "Annual", nor "1234" in "12 34". A one-letter word counts only
    within the whole text, as `any_word_left` reads one.
    """
    line = " ".join(spelled.split())
    looked_for = [
        word
        for text in words
        for word in [*(word for word in text.split() if len(word) > 1), " ".join(text.split())]
    ]
    return any(re.search(_whole_word(word), line) for word in looked_for)


def _whole_word(word: str) -> str:
    """`word` as a pattern: no letter or digit just before or after it."""
    return rf"(?<![^\W_]){re.escape(word)}(?![^\W_])"


def _rest_now(
    went: list[DrawnGlyph], *, found: list[DrawnGlyph], shown: list[DrawnGlyph]
) -> RestOfLine:
    """What became of the rest of a line whose glyphs `went` from where they were.

    `found` are the rests' glyphs found set along their line, `shown` those still on the page.
    """
    if not went:
        return "in_place"
    if not all(_holds(found, glyph) for glyph in went):
        return "removed"
    return "moved" if any(_holds(shown, glyph) for glyph in went) else "removed"


def _one_line(
    matched: list[DrawnGlyph],
    along: list[tuple[list[DrawnGlyph], list[DrawnGlyph]]],
    *,
    went: list[list[DrawnGlyph]],
    before: list[list[DrawnGlyph]],
) -> bool:
    """Whether the rest of `matched`'s line moved as one: what moved by one shift, in order.

    What stays stays, and no glyph that moved crosses another, read along the line. `along`
    are the runs and the glyphs each was; `went` each rest's glyphs that moved, `before` all
    its glyphs as they were.
    """
    lines = [
        at for at, each in enumerate(went) if any(_holds(each, glyph) for glyph in matched)
    ]
    moving = [
        (run, was)
        for run, was in along
        if any(_holds(went[at], glyph) for at in lines for glyph in was)
    ]
    shifts = [_offset(was[0], run[0]) for run, was in moving]
    if any(math.dist(shift, shifts[0]) > MOVED_PT for shift in shifts):
        return False
    now = {
        id(was): glyph for run, glyphs in moving for glyph, was in zip(run, glyphs, strict=True)
    }
    return all(_in_order(before[at], now=now) for at in lines)


def _in_order(line: list[DrawnGlyph], *, now: dict[int, DrawnGlyph]) -> bool:
    """Whether `line`'s glyphs, read along it as they were, still come in that order.

    `now` is where each that moved is, by the identity of the glyph it was.
    """
    if not line:
        return True
    direction = line[0].direction
    was = sorted(line, key=lambda glyph: _along(direction, glyph.origin))
    places = [_along(direction, now.get(id(glyph), glyph).origin) for glyph in was]
    return all(later > earlier - MOVED_PT for earlier, later in pairwise(places))


def _lands_on(
    run: list[DrawnGlyph],
    was: list[DrawnGlyph],
    *,
    lined: list[tuple[list[DrawnGlyph], list[DrawnGlyph]]],
    now: list[DrawnGlyph],
    boxes: tuple[dict[int, Rect], dict[int, Rect]],
) -> bool:
    """Whether `run`, the glyphs `was` set along, covers another: the page would read broken.

    Any on its own line; on another, more than that same glyph covered it before it moved.
    Not a rest of `lined` set along by its shift, as a bold's copy. `boxes` are each glyph's
    box now and before the erase, by identity.
    """
    box_now, box_then = boxes
    shift = _offset(was[0], run[0])
    with_it = {
        id(glyph)
        for other, other_was in lined
        if math.dist(_offset(other_was[0], other[0]), shift) <= MOVED_PT
        for glyph in other
    }
    for glyph, other in product(run, now):
        along, across = _covered(box_now[id(glyph)], box_now[id(other)], glyph.direction)
        if id(other) in with_it or along <= MOVED_PT or across <= MOVED_PT:
            continue
        if _on_line_of(glyph, other):
            return True
        # The move made it, unless the same pair met as much before: their places then.
        then = _covered(box_then[id(glyph)], box_then[id(other)], glyph.direction)
        if along > then[0] + MOVED_PT or across > then[1] + MOVED_PT:
            return True
    return False


# A box's sides on a line: where it starts and ends along it, then across it, top first.
type _Sides = tuple[float, float, float, float]


def _by_glyph(glyphs: list[DrawnGlyph], boxes: list[Rect] | None) -> dict[int, Rect] | None:
    """Each of `glyphs`' box, by its identity; None unless `boxes` has one for each."""
    if boxes is None or len(boxes) != len(glyphs):
        return None
    return dict(zip(map(id, glyphs), boxes, strict=True))


def _covered(box: Rect, other: Rect, direction: tuple[float, float]) -> tuple[float, float]:
    """How far `box` covers `other`, along a line read along `direction` and across it."""
    mine, theirs = _sides(box, direction), _sides(other, direction)
    along = min(mine[1], theirs[1]) - max(mine[0], theirs[0])
    across = min(mine[3], theirs[3]) - max(mine[2], theirs[2])
    return along, across


def _sides(box: Rect, direction: tuple[float, float]) -> _Sides:
    """`box`'s sides on a line read along `direction`: a level one along x, a turned one y."""
    if abs(direction[0]) >= abs(direction[1]):
        return (box.x0, box.x1, box.y0, box.y1)
    return (box.y0, box.y1, box.x0, box.x1)


def _set_off(box: Rect, by: tuple[float, float]) -> Rect:
    """`box`, set `by` off where it is."""
    return Rect(box.x0 + by[0], box.y0 + by[1], box.x1 + by[0], box.y1 + by[1])


def _on_page(glyph: DrawnGlyph, size: Page) -> bool:
    """Whether `glyph` starts on a page of `size`."""
    x, y = glyph.origin
    return 0 <= x <= size.width and 0 <= y <= size.height


def _along(direction: tuple[float, float], point: tuple[float, float]) -> float:
    """How far along a line that reads along `direction` `point` is."""
    return point[0] * direction[0] + point[1] * direction[1]


def _holds(glyphs: list[DrawnGlyph], glyph: DrawnGlyph) -> bool:
    """Whether `glyphs` holds `glyph` itself, not one like it: two can be drawn alike."""
    return any(each is glyph for each in glyphs)


def _offset(was: DrawnGlyph, now: DrawnGlyph) -> tuple[float, float]:
    """How far `now` starts from where `was` did."""
    return (now.origin[0] - was.origin[0], now.origin[1] - was.origin[1])


def _across(direction: tuple[float, float], offset: tuple[float, float]) -> float:
    """How far `offset` goes across a line that reads along `direction`."""
    return offset[1] * direction[0] - offset[0] * direction[1]


def whole_words(runs: Iterable[RedactedRun]) -> re.Pattern[str]:
    """The runs' words as one pattern: whole words only, in any case, the longest first.

    In any case, since a hidden copy is typed, not the page's own letters. Longest first, so
    a whole text goes whole, its one-letter words with it. A run's words match joined to the
    line's words beside it too, either side, as a hidden copy may write them ("Acct1234").
    """
    choices = {choice for run in runs for choice in _word_choices(run)}
    longest_first = sorted(choices, key=lambda choice: len(choice[0]), reverse=True)
    return re.compile("|".join(map(_whole, longest_first)), re.IGNORECASE)


# A word to match, as a pattern, and the words a hidden copy may join to it, either side.
type _Choice = tuple[str, tuple[str, ...]]


def _whole(choice: _Choice) -> str:
    """`choice` as a pattern: no letter or digit just before or after it, but a word joined."""
    pattern, beside = choice
    start = "|".join([r"(?<![^\W_])", *(f"(?<={re.escape(word)})" for word in beside)])
    end = "|".join([r"(?![^\W_])", *(f"(?={re.escape(word)})" for word in beside)])
    return f"(?:{start})(?:{pattern})(?:{end})"


def _word_choices(run: RedactedRun) -> list[_Choice]:
    """`run`'s text whole, then each word of two letters or more, as patterns to match.

    Alone, "a" or "I" is in most sentences. The whole text matches its words with anything
    but letters and digits between, since a hidden copy may join them otherwise.
    """
    comparable = _comparable(run.text)
    words = _WORD.findall(comparable)
    beside = tuple(sorted({run.before, run.after} - {""}))
    whole = r"[\W_]*".join(map(re.escape, words or comparable.split()))
    return [(whole, beside), *((re.escape(word), beside) for word in words if len(word) > 1)]


def words_beside(text: str, asked: range) -> tuple[str, str]:
    """The words of `text` just before `asked` and just after it; empty where there's none."""
    before = _WORD.findall(_comparable(text[: asked.start]))
    after = _WORD.findall(_comparable(text[asked.stop :]))
    return (before[-1] if before else "", after[0] if after else "")


def _comparable(text: str) -> str:
    """`text` with each ligature or styled letter as its plain letters (NFKC).

    A page's letters keep their ligatures, and a typed hidden copy doesn't.
    """
    return unicodedata.normalize("NFKC", text)


def holds(words: re.Pattern[str], hidden_copy: str) -> bool:
    """Whether a hidden copy, or a link's action, holds any of `words`, ligatures as letters."""
    return words.search(_comparable(hidden_copy)) is not None


def _any_holds(words: re.Pattern[str], hidden_copies: list[str]) -> bool:
    """Whether any of `hidden_copies` holds one of `words`."""
    return any(holds(words, hidden_copy) for hidden_copy in hidden_copies)


def _places_holding(words: re.Pattern[str], copies: list[HiddenCopy]) -> list[CopyPlace]:
    """The places of the copies holding any of `words`, each once, in their named order."""
    held = {hidden_copy.place for hidden_copy in copies if holds(words, hidden_copy.text)}
    return [place for place in COPY_PLACES if place in held]


def without(words: re.Pattern[str], hidden_copy: str) -> str:
    """A hidden copy with `words` deleted and the spaces closed up; as it was if none are in it.

    What's left has its ligatures spelled out, as it was compared. Deleted until none is left:
    a word deleted can leave one beside it whole ("11" less a "1" joined to the line's other).
    """
    if not holds(words, hidden_copy):
        return hidden_copy
    left = _comparable(hidden_copy)
    while words.search(left):
        left = words.sub("", left)
    return " ".join(left.split())
