"""Every sentence the app says, each under a key, in every language we have.

A language is one file here, named by its tag: `en.toml` is English. Code below
the API names a sentence by its key in a Message; the edge says it in the
reader's language with `render`.

Placeholders are bare `{name}`, so the browser can fill them too. A key is
never renamed: the browser can branch on it.
"""

from __future__ import annotations

import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from importlib import resources
from importlib.resources.abc import Traversable

from squidpdf.core.app.message import Message, Param, SaidInfo

__all__ = [
    "ENGLISH",
    "load_catalogs",
    "CATALOGS",
    "ENGLISH_SENTENCES",
    "OPTION_KEYS",
    "sentence",
    "catalog",
    "render",
    "render_all",
    "said",
    "fill",
    "language_headers",
    "visible",
]

ENGLISH = "en"


def load_catalogs(folder: Traversable) -> dict[str, Mapping[str, str]]:
    """Every language file in `folder`, by its lower-case tag: `pt-BR.toml` answers "pt-br"."""
    return {
        file.name.removesuffix(".toml").lower(): tomllib.loads(file.read_text(encoding="utf-8"))
        for file in folder.iterdir()
        if file.name.endswith(".toml")
    }


# Every language we can answer in, by its tag; English is the fallback.
CATALOGS = load_catalogs(resources.files(__name__))
ENGLISH_SENTENCES = CATALOGS[ENGLISH]

# Each way out of an overflow, by the name an edit's `strategy` uses: its sentences' keys.
OPTION_KEYS: dict[str, dict[str, str]] = {
    "shrink": {"label": "shrink_label", "detail": "shrink_detail"},
    "condense": {"label": "condense_label", "detail": "condense_detail"},
    "as-is": {"label": "as_is_label", "detail": "as_is_detail"},
}


def sentence(key: str, language: str = ENGLISH, default: str | None = None) -> str:
    """The sentence under `key` in `language`, placeholders unfilled.

    A key the language lacks is said in English, then as `default`. Raises
    KeyError when there's none of those: a key no catalog has is a bug.
    """
    # .get: a language we have may not say every sentence yet, or not be one we have.
    in_english = ENGLISH_SENTENCES.get(key, default)
    found = CATALOGS.get(language, ENGLISH_SENTENCES).get(key, in_english)
    if found is None:
        raise KeyError(key)
    return found


def catalog(language: str) -> dict[str, str]:
    """Every sentence as `language` says it, English where it has none."""
    return {key: sentence(key, language) for key in ENGLISH_SENTENCES}


def render(message: Message, language: str = ENGLISH) -> str:
    """`message` as a person reads it in `language`: its sentence, placeholders filled."""
    return fill(sentence(message.key, language), message.params, language)


def said(message: Message, language: str) -> SaidInfo:
    """`message` as a reader gets it: said in `language` as `detail`, and unsaid beside it."""
    return {"detail": render(message, language), **message.as_info()}


def render_all(messages: Iterable[Message], language: str = ENGLISH) -> str | None:
    """Several messages as one line, in the order given; None when there are none."""
    joiner = sentence("join_parts", language)
    return joiner.join(render(message, language) for message in messages) or None


def fill(template: str, params: Mapping[str, Param], language: str = ENGLISH) -> str:
    """`template` with each placeholder's fact written out as a person reads it."""
    written = {name: written_out(name, value, language) for name, value in params.items()}
    return template.format_map(written)


def language_headers(language: str) -> dict[str, str]:
    """The headers a reply in words carries: its language, and `Vary` so no cache mixes them."""
    return {"Content-Language": language, "Vary": "Accept-Language"}


def written_out(name: str, value: Param, language: str) -> str:
    """One fact as it reads in a sentence: a list joined, a fraction to one decimal place."""
    if isinstance(value, list):
        # A list's own joiner, or the one every list without one takes.
        joiner = sentence(f"join_{name}", language, default=sentence("join_items", language))
        return joiner.join(visible(item, language) for item in value)
    if isinstance(value, float):
        # One decimal place, written with the language's own mark: "6.8", or "6,8" where
        # a comma is the decimal mark.
        return f"{value:.1f}".replace(".", sentence("decimal_separator", language))
    return str(value)


def visible(character: str, language: str = ENGLISH) -> str:
    """A character as a person can see it: itself, or its name when it draws nothing alone.

    Named in `language`, e.g. "narrow no-break space", when a catalog names
    it; else by Unicode's name, lower case; else, for a control character with
    no name, by its code point, e.g. "U+0001". An accent on its own is named
    too: in a sentence it would sit on the space before it.
    """
    alone = len(character) == 1
    blank = alone and (not character.isprintable() or character.isspace())
    shows = not blank and not (alone and unicodedata.combining(character))
    if shows:
        return character
    # Its name in `language` when the catalog has one, else Unicode's.
    return sentence(name_key(character), language, default=unicode_name(character))


def name_key(character: str) -> str:
    """The catalog key a character's name is under: U+202F's is `char_u202f`."""
    return f"char_u{ord(character):04x}"


def unicode_name(character: str) -> str:
    """Unicode's name for a character, lower case; its code point ("U+0001") if it has none."""
    try:
        return unicodedata.name(character).lower()
    except ValueError:  # control characters and unassigned code points have no name
        return f"U+{ord(character):04X}"
