"""What the app says is kept as a Message, and put into a language only when it's said."""

from __future__ import annotations

from string import Formatter

import pytest

from squidpdf.core import Message, words
from tests.conftest import pseudo_sentence
from tests.core.conftest import placeholders
from tests.helpers import assert_equal


def test_every_language_has_every_sentence_with_the_same_placeholders(pseudo):
    english = words.ENGLISH_SENTENCES
    for language, catalog in words.CATALOGS.items():
        assert_equal(set(catalog), set(english), f"the keys {language!r} has")
        for key, said in catalog.items():
            wants = placeholders(english[key])
            assert_equal(placeholders(said), wants, f"{language!r} {key!r} placeholders")


def test_every_placeholder_is_bare_so_the_browser_can_fill_it_too():
    for language, catalog in words.CATALOGS.items():
        dressed = [key for key, said in catalog.items() if _dressed(said)]
        assert_equal(dressed, [], f"{language!r} sentences the browser can't fill")


def _dressed(sentence: str) -> bool:
    """Whether a placeholder in `sentence` carries a format or a conversion, like `{n:.1f}`."""
    return any(
        name and (spec or conv) for _text, name, spec, conv in Formatter().parse(sentence)
    )


@pytest.mark.parametrize(
    ("message", "said"),
    [
        (Message("too_long", {"delta_pt": 3.14159}), "3.1 pt too long"),
        (
            Message("missing", {"chars": ["é", "ß"], "font": "Carlito Bold"}),
            "no é or ß in this font, so the line is drawn in Carlito Bold",
        ),
        (
            Message("left_out", {"letters": ["中", "文"]}),
            "Left out 中 文: no font we have can draw them.",
        ),
    ],
    ids=["a fraction", "characters", "letters"],
)
def test_a_message_is_said_in_english_with_its_facts_written_out(message, said):
    assert_equal(words.render(message), said, "the sentence")


def test_a_number_is_written_with_the_reader_s_decimal_separator(pseudo, monkeypatch):
    monkeypatch.setitem(words.CATALOGS[pseudo], "decimal_separator", ",")
    said = words.render(Message("too_long", {"delta_pt": 3.14159}), pseudo)
    assert_equal(said, pseudo_sentence("3,1 pt too long"), "a fraction in another language")


def test_a_list_with_no_joiner_of_its_own_is_joined_as_items():
    """Only `chars` and `letters` have their own; a new list must not fail when it's said."""
    said = words.fill("{fonts}", {"fonts": ["Carlito Bold", "Arimo Regular"]})
    assert_equal(said, "Carlito Bold, Arimo Regular", "a list with no join_fonts")


@pytest.mark.parametrize(
    ("character", "said"),
    [
        ("\u202f", "narrow no-break space"),
        ("\u2004", "three-per-em space"),
        ("\x01", "U+0001"),
        ("\u0301", "acute accent"),
    ],
    ids=[
        "one the catalog names",
        "one only Unicode names",
        "one with no name at all",
        "an accent on its own: it'd sit on the space",
    ],
)
def test_a_character_that_draws_nothing_is_named(character, said):
    fit = Message("missing", {"chars": ["é", character], "font": "Noto Sans Regular"})
    expected = f"no é or {said} in this font, so the line is drawn in Noto Sans Regular"
    assert_equal(words.render(fit), expected, "the sentence naming it")
    left_out = Message("left_out", {"letters": [character]})
    expected = f"Left out {said}: no font we have can draw them."
    assert_equal(words.render(left_out), expected, "the sentence naming it")


def test_a_character_is_named_in_the_reader_s_language(pseudo):
    said = words.render(Message("left_out", {"letters": ["\u202f"]}), pseudo)
    expected = pseudo_sentence("Left out narrow no-break space: no font we have can draw them.")
    assert_equal(said, expected, "the name, in the language asked for")


def test_every_character_name_in_the_catalog_is_said_for_its_character(pseudo):
    """A name's key is its character's code point; a typo in one would leave it unsaid."""
    names = {
        key: said for key, said in words.ENGLISH_SENTENCES.items() if key.startswith("char_")
    }
    unsaid = [
        key
        for key, said in names.items()
        if words.visible(chr(int(key.removeprefix("char_u"), 16)), pseudo)
        != pseudo_sentence(said)
    ]
    assert_equal(unsaid, [], "names in the catalog their character isn't said by")
