"""What the app says is kept as a Message, and put into a language only when it's said."""

from __future__ import annotations

from string import Formatter

import pytest

from squidpdf.core import Message, words
from tests.conftest import pseudo_sentence
from tests.core.conftest import placeholders
from tests.helpers import assert_equal


def _sentences_unlike_english() -> list[tuple[str, str, str]]:
    """Each sentence a language says unlike English: its language, its key and how."""
    english = words.CATALOGS[words.ENGLISH]
    unlike: list[tuple[str, str, str]] = []
    for language, catalog in words.CATALOGS.items():
        unlike += [(language, key, "missing") for key in english.keys() - catalog.keys()]
        unlike += [(language, key, "not in English") for key in catalog.keys() - english.keys()]
        unlike += [
            (language, key, "other placeholders")
            for key in catalog.keys() & english.keys()
            if placeholders(catalog[key]) != placeholders(english[key])
        ]
    return sorted(unlike)


def test_every_language_has_every_sentence_with_the_same_placeholders():
    assert_equal(_sentences_unlike_english(), [], "sentences a language says unlike English")


@pytest.mark.parametrize(
    ("key", "said", "how"),
    [
        ("too_long", None, "missing"),
        ("too_long", "TOO LONG", "other placeholders"),
        ("too_long", "{delta} PT TOO LONG", "other placeholders"),
        ("not_a_sentence", "NOT A SENTENCE", "not in English"),
    ],
    ids=["a sentence left out", "a placeholder left out", "a placeholder renamed", "an extra"],
)
def test_a_language_unlike_english_is_found(pseudo, monkeypatch, key, said, how):
    """The check above finds the one thing changed in a language made from English."""
    if said is None:
        monkeypatch.delitem(words.CATALOGS[pseudo], key)
    else:
        monkeypatch.setitem(words.CATALOGS[pseudo], key, said)
    assert_equal(_sentences_unlike_english(), [(pseudo, key, how)], "what the check finds")


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
    fit = Message("missing", {"chars": ["é"], "font": ["Carlito Bold", "Arimo Regular"]})
    expected = "no é in this font, so the line is drawn in Carlito Bold, Arimo Regular"
    assert_equal(words.render(fit), expected, "a list with no join_font")


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


def _left_out(character: str) -> Message:
    """The sentence an edit that left `character` out says, which names it."""
    return Message("left_out", {"letters": [character]})


def test_every_character_name_in_the_catalog_is_said_for_its_character(pseudo):
    """A name's key is its character's code point; a typo in one would leave it unsaid."""
    names = {
        key: said
        for key, said in words.CATALOGS[words.ENGLISH].items()
        if key.startswith("char_")
    }
    unsaid = [
        key
        for key, said in names.items()
        if words.render(_left_out(chr(int(key.removeprefix("char_u"), 16))), pseudo)
        != pseudo_sentence(f"Left out {said}: no font we have can draw them.")
    ]
    assert_equal(unsaid, [], "names in the catalog their character isn't said by")
