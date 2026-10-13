"""The reader's language: which catalog in `core.app.words` an answer is written in.

Chosen from the browser's Accept-Language, English when nothing it asks for is
here. Only the edge knows it: below the API everything is a Message.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from squidpdf.core import words

_WILDCARD = "*"  # "any language": ours first


def _best_language(accept_language: str | None) -> str:
    """The tag of the catalog to answer in: the most wanted one we have, else English.

    A tag we lack falls back to its shorter forms, so "en-GB" is answered in "en",
    unless the browser refused that form itself. Weights break ties in the order
    given; a tag weighted 0 is refused.
    """
    asked = [_weighed(item) for item in (accept_language or "").split(",")]
    named = [(tag, quality) for tag, quality in asked if tag]
    refused = {tag for tag, quality in named if quality == 0}
    # Most wanted first: the sort is stable, so equal weights keep the header's order.
    wanted = [tag for tag, quality in sorted(named, key=lambda pair: -pair[1]) if quality > 0]
    answers = (_catalog_for(tag, refused=refused) for tag in wanted)
    return next((catalog for catalog in answers if catalog is not None), words.ENGLISH)


def _weighed(item: str) -> tuple[str, float]:
    """One item of the header: its tag, in lower case, and how much it's wanted."""
    tag, _, weight = item.partition(";")
    return tag.strip().lower(), _quality_of(weight)


def _catalog_for(tag: str, *, refused: set[str]) -> str | None:
    """The catalog for `tag`: it, else the longest shorter form we have that isn't refused."""
    # Any language: ours first.
    if tag == _WILDCARD:
        return words.ENGLISH
    subtags = tag.split("-")
    forms = ("-".join(subtags[:length]) for length in range(len(subtags), 0, -1))
    return next(
        (form for form in forms if form in words.CATALOGS and form not in refused), None
    )


def _quality_of(weight: str) -> float:
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
    return _best_language(request.headers.get("accept-language"))


# A route's reader's language, as a parameter: `said_in: ReaderLanguage`.
ReaderLanguage = Annotated[str, Depends(language_of)]
