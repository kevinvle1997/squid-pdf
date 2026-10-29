"""The terms as the browser gets them: a title, when they last changed, and paragraphs."""

from __future__ import annotations

from typing import Literal, TypedDict


class TermsWords(TypedDict):
    """Words of a paragraph, shown as they're written."""

    kind: Literal["words"]
    text: str


class TermsEmail(TypedDict):
    """An email address in a paragraph, shown as a link to write to it."""

    kind: Literal["email"]
    address: str


# A plain alias, not a `type` statement: pydantic reads it (rules/backend/api.md, The wire).
TermsPiece = TermsWords | TermsEmail


class TermsParagraph(TypedDict):
    """One paragraph: its lead, set bold, then the rest in pieces."""

    lead: str
    pieces: list[TermsPiece]


class Terms(TypedDict):
    """The service's terms, as its deployment wrote them."""

    title: str
    last_changed: (
        str | None
    )  # the line under the title saying when, as written; None without one
    paragraphs: list[TermsParagraph]
