"""The reader's language: which catalog in `core.words` an answer is written in.

Chosen from the browser's Accept-Language, English when nothing it asks for is
here. Only the edge knows it: below the API everything is a Message.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from squidpdf.core import words

_WILDCARD = "*"  # "any language": ours first


def negotiate(accept_language: str | None) -> str:
    """The tag of the catalog to answer in: the most wanted one we have, else English.

    A tag we lack falls back to its shorter forms, so "en-GB" is answered in "en".
    Weights break ties in the order given; a tag weighted 0 is refused.
    """
    wanted: list[tuple[float, int, str]] = []
    for position, item in enumerate((accept_language or "").split(",")):
        tag, _, weight = item.partition(";")
        tag, quality = tag.strip().lower(), _quality(weight)
        if tag and quality > 0:
            wanted.append((-quality, position, tag))
    for _quality_first, _position, tag in sorted(wanted):
        if tag == _WILDCARD:
            return words.ENGLISH
        subtags = tag.split("-")
        for length in range(len(subtags), 0, -1):
            candidate = "-".join(subtags[:length])
            if candidate in words.CATALOGS:
                return candidate
    return words.ENGLISH


def _quality(weight: str) -> float:
    """The `q=` in what follows a tag: 1 when it isn't there, 0 when it can't be read."""
    for param in weight.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                quality = float(value)
            except ValueError:
                return 0.0
            # Past the range the header allows, or NaN, which fails both comparisons.
            return quality if 0 <= quality <= 1 else 0.0
    return 1.0


def of(request: Request) -> str:
    """The language this request is answered in."""
    return negotiate(request.headers.get("accept-language"))


def headers(language: str) -> dict[str, str]:
    """The headers a reply in words carries: its language, and that it depends on it.

    `Vary`, so no cache hands one reader's language to another.
    """
    return {"Content-Language": language, "Vary": "Accept-Language"}


# A route's reader's language, as a parameter: `said_in: Language`.
Language = Annotated[str, Depends(of)]
