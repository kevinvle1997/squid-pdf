from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from squidpdf.core.fonts.catalog import face_bytes
from squidpdf.core.fonts.coverage import Coverage, coverage_of
from squidpdf.core.fonts.look_alike import broadest
from squidpdf.core.types import Face


@dataclass(frozen=True, slots=True)
class Substitute:
    face: Face
    text: str
    left_out: list[str]


@cache
def face_coverage(face: Face) -> Coverage:
    return coverage_of(face_bytes(face))


@cache
def face_letters(face: Face) -> tuple[str, ...]:
    """Kept short for the browser's preview; letters past these ranges still draw."""
    return face_coverage(face).previewed_letters()


def substitute_for(look_alike: Face, text: str) -> Substitute:
    """One face for the whole line: two would look like a mistake."""
    in_look_alike = _drawn_in(look_alike, text)
    if not in_look_alike.left_out:
        return in_look_alike
    candidates = [in_look_alike, _drawn_in(broadest(look_alike), text)]
    # min keeps the first of a tie: the look-alike
    return min(candidates, key=lambda candidate: len(candidate.left_out))


def _drawn_in(face: Face, text: str) -> Substitute:
    left_out = face_coverage(face).missing(text)
    kept = "".join(ch for ch in text if ch not in left_out)
    return Substitute(face, kept, left_out)
