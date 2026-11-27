from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from squidpdf.core import words

_WILDCARD = "*"


def _best_language(accept_language: str | None) -> str:
    asked = [_weighed(item) for item in (accept_language or "").split(",")]
    named = [(tag, quality) for tag, quality in asked if tag]
    refused = {tag for tag, quality in named if quality == 0}
    # Most wanted first: the sort is stable, so equal weights keep the header's order.
    wanted = [tag for tag, quality in sorted(named, key=lambda pair: -pair[1]) if quality > 0]
    answers = (_catalog_for(tag, refused=refused) for tag in wanted)
    return next((catalog for catalog in answers if catalog is not None), words.ENGLISH)


def _weighed(item: str) -> tuple[str, float]:
    tag, _, weight = item.partition(";")
    return tag.strip().lower(), _quality_of(weight)


def _catalog_for(tag: str, *, refused: set[str]) -> str | None:
    if tag == _WILDCARD:
        return words.ENGLISH
    subtags = tag.split("-")
    forms = ("-".join(subtags[:length]) for length in range(len(subtags), 0, -1))
    return next(
        (form for form in forms if form in words.CATALOGS and form not in refused), None
    )


def _quality_of(weight: str) -> float:
    params = (param.partition("=") for param in weight.split(";"))
    written = next((value for name, _, value in params if name.strip().lower() == "q"), None)
    if written is None:
        return 1.0
    return _quality_written_as(written)


def _quality_written_as(written: str) -> float:
    try:
        quality = float(written)
    except ValueError:
        return 0.0
    # Past the range the header allows, or NaN, which fails both comparisons.
    return quality if 0 <= quality <= 1 else 0.0


def language_of(request: Request) -> str:
    return _best_language(request.headers.get("accept-language"))


ReaderLanguage = Annotated[str, Depends(language_of)]
