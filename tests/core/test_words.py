"""What the app says is kept as a Message, and put into a language only when it's said."""

from __future__ import annotations

import pickle
import re
from string import Formatter

import pytest

from squidpdf.core import Message, words
from tests.conftest import pseudo_sentence
from tests.helpers import assert_equal, assert_true

_SNAKE_CASE = re.compile(r"[a-z]+(_[a-z]+)*")


def _placeholders(sentence: str) -> set[str]:
    """The `{name}`s a sentence takes."""
    return {name for _text, name, _spec, _conv in Formatter().parse(sentence) if name}


def test_every_language_has_every_sentence_with_the_same_placeholders(pseudo):
    english = words.ENGLISH_SENTENCES
    for language, catalog in words.CATALOGS.items():
        assert_equal(set(catalog), set(english), f"the keys {language!r} has")
        for key, said in catalog.items():
            wants = _placeholders(english[key])
            assert_equal(_placeholders(said), wants, f"{language!r} {key!r} placeholders")


def test_every_key_is_snake_case():
    bad = [key for key in words.ENGLISH_SENTENCES if not _SNAKE_CASE.fullmatch(key)]
    assert_equal(bad, [], "keys that aren't snake_case")


def test_every_placeholder_is_bare_so_the_browser_can_fill_it_too():
    for key, said in words.ENGLISH_SENTENCES.items():
        specs = [(spec, conv) for _t, name, spec, conv in Formatter().parse(said) if name]
        assert_true(all(spec == "" and conv is None for spec, conv in specs), f"{key}: {said}")


@pytest.mark.parametrize(
    ("message", "said"),
    [
        (Message("no_span"), words.NO_SPAN),
        (Message("too_long", {"delta_pt": 3.14159}), "3.1 pt too long"),
        (Message("too_large", {"mb": 100}), "This file is over 100 MB."),
        (
            Message("missing", {"chars": ["é", "ß"], "font": "Carlito Bold"}),
            "no é or ß in this font, so the line is drawn in Carlito Bold",
        ),
        (
            Message("left_out", {"letters": ["中", "文"]}),
            "Left out 中 文: no font we have can draw them.",
        ),
        (
            Message("text_too_long", {"chars": 1000}),
            "New text can be up to 1000 characters.",
        ),
    ],
    ids=[
        "no facts",
        "a fraction",
        "a whole number",
        "characters",
        "letters",
        "a count of characters",
    ],
)
def test_a_message_is_said_in_english_with_its_facts_written_out(message, said):
    assert_equal(words.render(message), said, "the sentence")


@pytest.mark.parametrize(
    ("character", "said"),
    [
        ("\u202f", "narrow no-break space"),
        (" ", "space"),
        ("\u00ad", "soft hyphen"),
        ("\u200b", "zero width space"),
        ("\t", "U+0009"),
        ("\u0301", "\u0301"),
    ],
    ids=["narrow no-break space", "space", "soft hyphen", "zero width", "tab", "an accent"],
)
def test_a_character_that_draws_nothing_is_named(character, said):
    fit = Message("missing", {"chars": ["é", character], "font": "Noto Sans Regular"})
    expected = f"no é or {said} in this font, so the line is drawn in Noto Sans Regular"
    assert_equal(words.render(fit), expected, "the sentence naming it")
    left_out = Message("left_out", {"letters": [character]})
    expected = f"Left out {said}: no font we have can draw them."
    assert_equal(words.render(left_out), expected, "the sentence naming it")


def test_a_message_is_said_in_the_language_asked_for_joiners_and_all(pseudo):
    message = Message("missing", {"chars": ["é", "ß"], "font": "Carlito Bold"})
    expected = "NO é OR ß IN THIS FONT, SO THE LINE IS DRAWN IN Carlito Bold"
    assert_equal(words.render(message, pseudo), expected, "the sentence in the pseudo-language")
    parts = [Message("no_span"), Message("no_page")]
    joined = f"{pseudo_sentence(words.NO_SPAN)}; {pseudo_sentence(words.NO_PAGE)}"
    assert_equal(words.render_all(parts, pseudo), joined, "two messages in one line")


def test_a_sentence_a_language_lacks_is_said_in_english(pseudo, monkeypatch):
    lacking = {key: said for key, said in words.CATALOGS[pseudo].items() if key != "no_span"}
    monkeypatch.setitem(words.CATALOGS, pseudo, lacking)
    assert_equal(words.render(Message("no_span"), pseudo), words.NO_SPAN, "the fallback")


def test_a_language_we_have_no_catalog_for_is_answered_in_english():
    assert_equal(words.render(Message("no_span"), "fr"), words.NO_SPAN, "the fallback")


def test_a_key_no_catalog_has_is_a_bug():
    with pytest.raises(KeyError):
        words.render(Message("no_such_sentence"))


def test_nothing_is_said_when_there_is_nothing_to_say():
    assert_true(words.render_all([]) is None, "render_all of no messages")


def test_a_message_survives_the_trip_from_a_worker_and_to_disk():
    message = Message("left_out", {"letters": ["中"], "count": 1, "delta_pt": 2.5})
    assert_equal(pickle.loads(pickle.dumps(message)), message, "after a pickle round trip")
    assert_equal(Message.from_info(message.as_info()), message, "after a JSON round trip")
    expected = {"code": "left_out", "params": {"letters": ["中"], "count": 1, "delta_pt": 2.5}}
    assert_equal(message.as_info(), expected, "as JSON")
