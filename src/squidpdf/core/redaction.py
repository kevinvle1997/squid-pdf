from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from itertools import pairwise, product

import squidpdf.core.constants as constants
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

_WORD = re.compile(r"[^\W_]+")
_EVERYWHERE = Rect(-math.inf, -math.inf, math.inf, math.inf)
_SAME_WAY = 0.01

type _RunAlong = tuple[list[DrawnGlyph], list[DrawnGlyph]]


@dataclass(frozen=True, slots=True, eq=False)
class RedactionCheck:
    driver: PdfDriver

    def hidden_places(self, runs: Iterable[RedactedRun]) -> dict[str, list[HiddenPlace]]:
        """A signed document names its signatures for every span: any redaction takes them."""
        asked = list(runs)
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
        [letters] = self.driver.letters_in(span.page, [span.bbox])
        return [
            letter for letter in letters if abs(letter.size - span.size) <= constants.SIZE_EPS
        ]

    def still_there(self, spans: Iterable[Span]) -> list[Span]:
        """Part of a card number is as much a leak as all of it; spaces are ignored."""
        return asked_by_page(spans, self._left_on)

    def _left_on(self, page: int, spans: list[Span]) -> list[Span]:
        texts = self.driver.text_in(
            page, [span.bbox.grown(constants.ROUNDED_PT) for span in spans]
        )
        pairs = zip(spans, texts, strict=True)
        return [span for span, left in pairs if any_word_left(span.text, left)]

    def kept_by(self, page: int, erased: Sequence[Rect]) -> list[DrawnGlyph]:
        reached = [box.grown(constants.ROUNDED_PT) for box in erased]
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
        drawn = self.driver.drawn_glyphs(page)
        now = _letters_of(drawn)
        gone = _less(list(kept), now, near=_same_place)
        new = _less(now, kept, near=_same_place)
        went = [[glyph for glyph in gone if _starts_on_line_of(glyph, span)] for span in rests]

        runs = _runs(_not_on(new, lines), among=now)
        spelling = self._spelling(page, runs, words=words)
        others = [run for run in runs if not any(run is each for each in spelling)]

        lined = _set_along_rests(others, gone=gone, kept=kept, rests=rests, went=went)
        rest, unchecked = self._rests_landed(page, lined, drawn=drawn, now=now)

        off = [run for run in others if not any(run is each for each, _matched in rest)]
        moved = _moved(off, gone=gone, kept=kept)
        taken = spelling + [run for run in off if not any(run is each for each in moved)]

        size = self.driver.pages()[page]
        return DrawnElsewhere(
            taken=[glyph for run in taken for glyph in run],
            moved=[glyph for run in moved for glyph in run],
            rests=_rests_now(went, landed=rest, size=size),
            unchecked=unchecked,
        )

    def _spelling(
        self, page: int, runs: list[list[DrawnGlyph]], *, words: Sequence[str]
    ) -> list[list[DrawnGlyph]]:
        reading = self.driver.letters_in(page, [_EVERYWHERE])[0] if runs else []
        return [run for run in runs if _spells_any(_spelled(run, reading), words)]

    def _rests_landed(
        self,
        page: int,
        lined: list[_RunAlong],
        *,
        drawn: list[DrawnGlyph],
        now: list[DrawnGlyph],
    ) -> tuple[list[_RunAlong], bool]:
        # Read only where some rest moved: a box for each glyph costs a reading of its own.
        box_of = _by_glyph(drawn, self.driver.drawn_glyph_boxes(page)) if lined else {}
        unchecked = box_of is None
        box_now = box_of or {}
        box_then = box_now | {
            id(glyph): _set_off(box_now[id(glyph)], _offset(run[0], matched[0]))
            for run, matched in ([] if unchecked else lined)
            for glyph in run
        }
        rest = [
            (run, matched)
            for run, matched in lined
            if unchecked
            or not _lands_on(run, matched, lined=lined, now=now, boxes=(box_now, box_then))
        ]
        return rest, unchecked

    def still_hidden(self, runs: Iterable[RedactedRun]) -> list[RedactedRun]:
        return asked_by_page(runs, self._hidden_on)

    def _hidden_on(self, page: int, runs: list[RedactedRun]) -> list[RedactedRun]:
        hidden_copies = self.driver.hidden_copies(page)
        return [run for run in runs if _any_holds(whole_words([run]), hidden_copies)]

    def still_hidden_in_document(self, runs: Iterable[RedactedRun]) -> list[RedactedRun]:
        checked = list(runs)
        if not checked:
            return []
        document_copies = self.driver.document_hidden_copies(
            partial(holds, whole_words(checked))
        )
        return [run for run in checked if _places_holding(whole_words([run]), document_copies)]


def words_around(text: str, asked: range) -> range | None:
    """The browser widens a selection the same way."""
    start, end = asked.start, asked.stop
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return None
    while start > 0 and _is_word_letter(text[start - 1]) and _is_word_letter(text[start]):
        start -= 1
    while end < len(text) and _is_word_letter(text[end - 1]) and _is_word_letter(text[end]):
        end += 1
    return range(start, end)


def _is_word_letter(letter: str) -> bool:
    """Not `_WORD`, which leaves marks out: a selection widens as the browser's does."""
    return unicodedata.category(letter)[0] in "LNM"


def any_word_left(text: str, left: str) -> bool:
    """A one-letter word counts only within the whole text: redaction labels often hold "A"."""
    leftover = "".join(left.split())
    words = [word for word in text.split() if _counts_alone(word)]
    return any(word in leftover for word in [*words, "".join(text.split())])


def _counts_alone(word: str) -> bool:
    return len(word) > 1


def _not_on(glyphs: list[DrawnGlyph], lines: Sequence[DrawnLine]) -> list[DrawnGlyph]:
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
    cos, sin = QUARTER_TURNS[line.turn_ccw]
    # The page's y grows downward.
    direction = (cos, -sin)
    offset = (glyph.origin[0] - line.origin[0], glyph.origin[1] - line.origin[1])
    along = offset[0] * direction[0] + offset[1] * direction[1]
    return (
        abs(_across(direction, offset)) <= constants.MOVED_PT and along >= -constants.MOVED_PT
    )


def _letters_of(glyphs: list[DrawnGlyph]) -> list[DrawnGlyph]:
    return [glyph for glyph in glyphs if not glyph.letter.isspace()]


def _less[Glyph: (DrawnGlyph, PlacedLetter), Other: (DrawnGlyph, PlacedLetter)](
    glyphs: list[Glyph],
    others: Sequence[Other],
    *,
    near: Callable[[Other, Glyph], bool],
) -> list[Glyph]:
    spares: dict[str, list[Other]] = {}
    for other in others:
        spares.setdefault(other.letter, []).append(other)
    left: list[Glyph] = []
    for glyph in glyphs:
        spare = spares.get(glyph.letter, [])
        found = [at for at, other in enumerate(spare) if near(other, glyph)]
        if not found:
            left.append(glyph)
            continue
        spare.pop(found[0])
    return left


def _same_place(was: DrawnGlyph | PlacedLetter, now: DrawnGlyph | PlacedLetter) -> bool:
    return math.dist(was.origin, now.origin) <= constants.MOVED_PT


def _runs(glyphs: list[DrawnGlyph], *, among: Sequence[DrawnGlyph]) -> list[list[DrawnGlyph]]:
    runs: list[list[DrawnGlyph]] = []
    at = -1
    for glyph in glyphs:
        before = at
        at = next(place for place in range(at + 1, len(among)) if among[place] is glyph)
        joins = bool(runs) and before == at - 1 and _starts_on_line_of(glyph, runs[-1][-1])
        if joins:
            runs[-1].append(glyph)
            continue
        runs.append([glyph])
    return runs


def _starts_on_line_of(glyph: DrawnGlyph, start: DrawnGlyph | Span) -> bool:
    offset = (glyph.origin[0] - start.origin[0], glyph.origin[1] - start.origin[1])
    same_way = math.dist(start.direction, glyph.direction) <= _SAME_WAY
    return same_way and abs(_across(start.direction, offset)) <= constants.MOVED_PT


def _part_of(run: list[DrawnGlyph], lines: list[list[DrawnGlyph]]) -> list[DrawnGlyph] | None:
    for line in lines:
        for start, first in enumerate(line):
            if first.letter != run[0].letter:
                continue
            matched = _matched_after(run, line, start=start)
            if matched is not None:
                return _taken_out(line, matched)
    return None


def _set_along(run: list[DrawnGlyph], lines: list[list[DrawnGlyph]]) -> list[DrawnGlyph] | None:
    for line in lines:
        for start, first in enumerate(line):
            shift = _offset(first, run[0])
            if (
                first.letter != run[0].letter
                or abs(_across(first.direction, shift)) > constants.MOVED_PT
            ):
                continue
            matched = _matched_after(run, line, start=start, shift=shift)
            if matched is not None:
                return _taken_out(line, matched)
    return None


def _taken_out(line: list[DrawnGlyph], places: list[int]) -> list[DrawnGlyph]:
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
    matched: list[int] = []
    at = start
    for glyph in run:
        found = next(
            (
                place
                for place in range(at, len(line))
                if line[place].letter == glyph.letter
                and (
                    shift is None
                    or math.dist(_offset(line[place], glyph), shift) <= constants.MOVED_PT
                )
            ),
            None,
        )
        if found is None:
            return None
        matched.append(found)
        at = found + 1
    return matched


def _spelled(run: list[DrawnGlyph], reading: list[PlacedLetter]) -> str:
    first = _read_at(run[0], reading, start=0)
    last = None if first is None else _read_at(run[-1], reading, start=first)
    if first is None or last is None:
        return "".join(glyph.letter for glyph in run)
    return "".join(letter.letter for letter in reading[first : last + 1])


def _read_at(glyph: DrawnGlyph, reading: list[PlacedLetter], *, start: int) -> int | None:
    return next(
        (
            at
            for at in range(start, len(reading))
            if reading[at].letter == glyph.letter and _same_place(reading[at], glyph)
        ),
        None,
    )


def _spells_any(spelled: str, words: Sequence[str]) -> bool:
    """Whole: "Ann" isn't in "Annual", nor "1234" in "12 34"."""
    line = " ".join(spelled.split())
    looked_for = [
        word
        for text in words
        for word in [
            *(word for word in text.split() if _counts_alone(word)),
            " ".join(text.split()),
        ]
    ]
    return any(re.search(_whole_word(word), line) for word in looked_for)


def _whole_word(word: str) -> str:
    return rf"(?<![^\W_]){re.escape(word)}(?![^\W_])"


def _set_along_rests(
    others: list[list[DrawnGlyph]],
    *,
    gone: list[DrawnGlyph],
    kept: Sequence[DrawnGlyph],
    rests: Sequence[Span],
    went: list[list[DrawnGlyph]],
) -> list[_RunAlong]:
    before = [[glyph for glyph in kept if _starts_on_line_of(glyph, span)] for span in rests]
    on_lines = [glyph for glyph in gone if any(_holds(each, glyph) for each in went)]
    rest_lines = _runs(on_lines, among=kept)
    along = [
        (run, matched) for run in others if (matched := _set_along(run, rest_lines)) is not None
    ]
    return [
        (run, matched)
        for run, matched in along
        if _one_line(matched, along, went=went, before=before)
    ]


def _moved(
    off: list[list[DrawnGlyph]], *, gone: list[DrawnGlyph], kept: Sequence[DrawnGlyph]
) -> list[list[DrawnGlyph]]:
    gone_lines = _runs(gone, among=kept)
    return [run for run in off if _part_of(run, gone_lines) is not None]


def _rests_now(
    went: list[list[DrawnGlyph]], *, landed: list[_RunAlong], size: Page
) -> list[RestOfLine]:
    found = [glyph for _run, matched in landed for glyph in matched]
    shown = [
        was
        for run, matched in landed
        for glyph, was in zip(run, matched, strict=True)
        if _on_page(glyph, size)
    ]
    return [_rest_now(each, found=found, shown=shown) for each in went]


def _rest_now(
    went: list[DrawnGlyph], *, found: list[DrawnGlyph], shown: list[DrawnGlyph]
) -> RestOfLine:
    if not went:
        return "in_place"
    if not all(_holds(found, glyph) for glyph in went):
        return "removed"
    return "moved" if any(_holds(shown, glyph) for glyph in went) else "removed"


def _one_line(
    matched: list[DrawnGlyph],
    along: list[_RunAlong],
    *,
    went: list[list[DrawnGlyph]],
    before: list[list[DrawnGlyph]],
) -> bool:
    lines = [
        at for at, each in enumerate(went) if any(_holds(each, glyph) for glyph in matched)
    ]
    moving = [
        (run, was)
        for run, was in along
        if any(_holds(went[at], glyph) for at in lines for glyph in was)
    ]
    shifts = [_offset(was[0], run[0]) for run, was in moving]
    if any(math.dist(shift, shifts[0]) > constants.MOVED_PT for shift in shifts):
        return False
    now = {
        id(was): glyph for run, glyphs in moving for glyph, was in zip(run, glyphs, strict=True)
    }
    return all(_in_order(before[at], now=now) for at in lines)


def _in_order(line: list[DrawnGlyph], *, now: dict[int, DrawnGlyph]) -> bool:
    if not line:
        return True
    direction = line[0].direction
    was = sorted(line, key=lambda glyph: _along(direction, glyph.origin))
    places = [_along(direction, now.get(id(glyph), glyph).origin) for glyph in was]
    return all(later > earlier - constants.MOVED_PT for earlier, later in pairwise(places))


def _lands_on(
    run: list[DrawnGlyph],
    was: list[DrawnGlyph],
    *,
    lined: list[_RunAlong],
    now: list[DrawnGlyph],
    boxes: tuple[dict[int, Rect], dict[int, Rect]],
) -> bool:
    box_now, box_then = boxes
    shift = _offset(was[0], run[0])
    with_it = {
        id(glyph)
        for other, other_was in lined
        if math.dist(_offset(other_was[0], other[0]), shift) <= constants.MOVED_PT
        for glyph in other
    }
    for glyph, other in product(run, now):
        along, across = _covered(box_now[id(glyph)], box_now[id(other)], glyph.direction)
        if id(other) in with_it or along <= constants.MOVED_PT or across <= constants.MOVED_PT:
            continue
        if _starts_on_line_of(other, glyph):
            return True
        then = _covered(box_then[id(glyph)], box_then[id(other)], glyph.direction)
        if along > then[0] + constants.MOVED_PT or across > then[1] + constants.MOVED_PT:
            return True
    return False


type _Sides = tuple[float, float, float, float]


def _by_glyph(glyphs: list[DrawnGlyph], boxes: list[Rect] | None) -> dict[int, Rect] | None:
    if boxes is None or len(boxes) != len(glyphs):
        return None
    return dict(zip(map(id, glyphs), boxes, strict=True))


def _covered(box: Rect, other: Rect, direction: tuple[float, float]) -> tuple[float, float]:
    mine, theirs = _sides(box, direction), _sides(other, direction)
    along = min(mine[1], theirs[1]) - max(mine[0], theirs[0])
    across = min(mine[3], theirs[3]) - max(mine[2], theirs[2])
    return along, across


def _sides(box: Rect, direction: tuple[float, float]) -> _Sides:
    if abs(direction[0]) >= abs(direction[1]):
        return (box.x0, box.x1, box.y0, box.y1)
    return (box.y0, box.y1, box.x0, box.x1)


def _set_off(box: Rect, by: tuple[float, float]) -> Rect:
    return Rect(box.x0 + by[0], box.y0 + by[1], box.x1 + by[0], box.y1 + by[1])


def _on_page(glyph: DrawnGlyph, size: Page) -> bool:
    x, y = glyph.origin
    return 0 <= x <= size.width and 0 <= y <= size.height


def _along(direction: tuple[float, float], point: tuple[float, float]) -> float:
    return point[0] * direction[0] + point[1] * direction[1]


def _holds(glyphs: list[DrawnGlyph], glyph: DrawnGlyph) -> bool:
    return any(each is glyph for each in glyphs)


def _offset(was: DrawnGlyph, now: DrawnGlyph) -> tuple[float, float]:
    return (now.origin[0] - was.origin[0], now.origin[1] - was.origin[1])


def _across(direction: tuple[float, float], offset: tuple[float, float]) -> float:
    return offset[1] * direction[0] - offset[0] * direction[1]


def whole_words(runs: Iterable[RedactedRun]) -> re.Pattern[str]:
    """In any case, since a hidden copy is typed, not the page's own letters."""
    choices = {choice for run in runs for choice in _word_choices(run)}
    longest_first = sorted(choices, key=lambda choice: len(choice[0]), reverse=True)
    return re.compile("|".join(map(_whole, longest_first)), re.IGNORECASE)


type _Choice = tuple[str, tuple[str, ...]]


def _whole(choice: _Choice) -> str:
    pattern, beside = choice
    start = "|".join([r"(?<![^\W_])", *(f"(?<={re.escape(word)})" for word in beside)])
    end = "|".join([r"(?![^\W_])", *(f"(?={re.escape(word)})" for word in beside)])
    return f"(?:{start})(?:{pattern})(?:{end})"


def _word_choices(run: RedactedRun) -> list[_Choice]:
    comparable = _comparable(run.text)
    words = _WORD.findall(comparable)
    beside = tuple(sorted({run.before, run.after} - {""}))
    whole = r"[\W_]*".join(map(re.escape, words or comparable.split()))
    return [
        (whole, beside),
        *((re.escape(word), beside) for word in words if _counts_alone(word)),
    ]


def words_beside(text: str, asked: range) -> tuple[str, str]:
    before = _WORD.findall(_comparable(text[: asked.start]))
    after = _WORD.findall(_comparable(text[asked.stop :]))
    return (before[-1] if before else "", after[0] if after else "")


def _comparable(text: str) -> str:
    """A page's letters keep their ligatures, and a typed hidden copy doesn't."""
    return unicodedata.normalize("NFKC", text)


def holds(words: re.Pattern[str], hidden_copy: str) -> bool:
    return words.search(_comparable(hidden_copy)) is not None


def _any_holds(words: re.Pattern[str], hidden_copies: list[str]) -> bool:
    return any(holds(words, hidden_copy) for hidden_copy in hidden_copies)


def _places_holding(words: re.Pattern[str], copies: list[HiddenCopy]) -> list[CopyPlace]:
    held = {hidden_copy.place for hidden_copy in copies if holds(words, hidden_copy.text)}
    return [place for place in COPY_PLACES if place in held]


def without(words: re.Pattern[str], hidden_copy: str) -> str:
    """Deleted until none is left: a word deleted can leave one beside it whole."""
    if not holds(words, hidden_copy):
        return hidden_copy
    left = _comparable(hidden_copy)
    while words.search(left):
        left = words.sub("", left)
    return " ".join(left.split())
