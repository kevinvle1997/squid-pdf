"""The reader's language: which catalog in `core.words` an answer is written in.

Chosen from the browser's Accept-Language, English when nothing it asks for is
here. Only the edge knows it: below the API everything is a Message.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from squidpdf.core import words

__all__ = [
    "best_language",
    "language_of",
    "ReaderLanguage",
]

_WILDCARD = "*"  # "any language": ours first


def best_language(accept_language: str | None) -> str:
    """The tag of the catalog to answer in: the most wanted one we have, else English.

    A tag we lack falls back to its shorter forms, so "en-GB" is answered in "en",
    unless the browser refused that form itself. Weights break ties in the order
    given; a tag weighted 0 is refused.
    """
    wanted: list[tuple[float, int, str]] = []
    refused: set[str] = set()
    for position, item in enumerate((accept_language or "").split(",")):
        tag, _, weight = item.partition(";")
        tag, quality = tag.strip().lower(), quality_of(weight)
        if tag and quality > 0:
            wanted.append((-quality, position, tag))
        if tag and quality == 0:
            refused.add(tag)
    for _quality_first, _position, tag in sorted(wanted):
        if tag == _WILDCARD:
            return words.ENGLISH
        subtags = tag.split("-")
        for length in range(len(subtags), 0, -1):
            candidate = "-".join(subtags[:length])
            if candidate in words.CATALOGS and candidate not in refused:
                return candidate
    return words.ENGLISH


def quality_of(weight: str) -> float:
    """The `q=` in what follows a tag: 1 when it isn't there, 0 when it can't be read."""
    for param in weight.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                quality = float(value)
            except ValueError:  # not a number, like "q=high"
                return 0.0
            # Past the range the header allows, or NaN, which fails both comparisons.
            return quality if 0 <= quality <= 1 else 0.0
    return 1.0


def language_of(request: Request) -> str:
    """The language this request is answered in."""
    return best_language(request.headers.get("accept-language"))


# A route's reader's language, as a parameter: `said_in: ReaderLanguage`.
ReaderLanguage = Annotated[str, Depends(language_of)]
