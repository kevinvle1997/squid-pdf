from __future__ import annotations

from typing import Literal, TypedDict


class TermsWords(TypedDict):
    kind: Literal["words"]
    text: str


class TermsEmail(TypedDict):
    kind: Literal["email"]
    address: str


# A plain alias, not a `type` statement: pydantic reads it.
TermsPiece = TermsWords | TermsEmail


class TermsParagraph(TypedDict):
    lead: str
    pieces: list[TermsPiece]


class Terms(TypedDict):
    title: str
    last_changed: str | None
    paragraphs: list[TermsParagraph]
