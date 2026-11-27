"""Placeholders are bare `{name}`, so the browser can fill them too."""

from __future__ import annotations

import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from importlib import resources
from importlib.resources.abc import Traversable

from squidpdf.core.app.message import Message, Param, SaidInfo

ENGLISH = "en"

_ONE_PLACE = Decimal("0.1")
_NAME_PREFIX = "char_u"
_QUOTED_CATEGORIES = frozenset("PS")

type CatalogEntry = str | Mapping[str, str]


def _load_catalogs(folder: Traversable) -> dict[str, Mapping[str, CatalogEntry]]:
    return {
        file.name.removesuffix(".toml").lower(): tomllib.loads(file.read_text(encoding="utf-8"))
        for file in folder.iterdir()
        if file.name.endswith(".toml")
    }


CATALOGS = _load_catalogs(resources.files(__name__))
_ENGLISH_SENTENCES = CATALOGS[ENGLISH]


def sentence(key: str, language: str = ENGLISH, default: str | None = None) -> str:
    found = _entry_in(key, language, default)
    if not isinstance(found, str):
        raise TypeError(f"{key!r} is said with a count: forms() has it")
    return found


def forms(key: str, language: str = ENGLISH) -> Mapping[str, str]:
    found = _entry_in(key, language, None)
    if isinstance(found, str):
        raise TypeError(f"{key!r} is said without a count: sentence() has it")
    return found


def _entry_in(key: str, language: str, default: str | None) -> CatalogEntry:
    in_english = _ENGLISH_SENTENCES.get(key, default)
    found = CATALOGS.get(language, _ENGLISH_SENTENCES).get(key, in_english)
    if found is None:
        raise KeyError(key)
    return found


def catalog(language: str) -> dict[str, CatalogEntry]:
    return {key: _entry_in(key, language, None) for key in _ENGLISH_SENTENCES}


def character_names(language: str) -> dict[str, str]:
    return {
        chr(int(key.removeprefix(_NAME_PREFIX), 16)): sentence(key, language)
        for key in _ENGLISH_SENTENCES
        if key.startswith(_NAME_PREFIX)
    }


def render(message: Message, language: str = ENGLISH) -> str:
    return _fill(sentence(message.key, language), message.params, language)


def said(message: Message, language: str) -> SaidInfo:
    return {"detail": render(message, language), **message.as_info()}


def render_all(messages: Iterable[Message], language: str = ENGLISH) -> str | None:
    joiner = sentence("join_parts", language)
    return joiner.join(render(message, language) for message in messages) or None


def render_sentences(messages: Iterable[Message], language: str = ENGLISH) -> str | None:
    joiner = sentence("join_sentences", language)
    return joiner.join(render(message, language) for message in messages) or None


def _fill(template: str, params: Mapping[str, Param], language: str = ENGLISH) -> str:
    written = {name: _written_out(name, value, language) for name, value in params.items()}
    return template.format_map(written)


def language_headers(language: str) -> dict[str, str]:
    return {"Content-Language": language, "Vary": "Accept-Language"}


def _written_out(name: str, value: Param, language: str) -> str:
    if isinstance(value, list):
        joiner = sentence(f"join_{name}", language, default=sentence("join_items", language))
        return joiner.join(_visible(item, language) for item in value)
    if isinstance(value, float):
        # A half rounds up from its shortest decimal form, as the browser does.
        one_place = Decimal(str(value)).quantize(_ONE_PLACE, rounding=ROUND_HALF_UP)
        return str(one_place).replace(".", sentence("decimal_separator", language))
    return str(value)


def _visible(character: str, language: str = ENGLISH) -> str:
    """An accent alone is named: in a sentence it would sit on the space before it."""
    alone = len(character) == 1
    if alone and _name_key(character) in _ENGLISH_SENTENCES:
        return sentence(_name_key(character), language)
    blank = alone and (not character.isprintable() or character.isspace())
    shows = not blank and not (alone and unicodedata.combining(character))
    if shows and alone and unicodedata.category(character)[0] in _QUOTED_CATEGORIES:
        return _fill(sentence("quoted", language), {"char": character}, language)
    if shows:
        return character
    return _unicode_name(character)


def _name_key(character: str) -> str:
    return f"{_NAME_PREFIX}{ord(character):04x}"


def _unicode_name(character: str) -> str:
    try:
        return unicodedata.name(character).lower()
    except ValueError:
        return f"U+{ord(character):04X}"
