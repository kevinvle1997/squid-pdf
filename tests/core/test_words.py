"""What the app says is kept as a Message, and put into a language only when it's said."""

from __future__ import annotations

from string import Formatter

import pytest

from squidpdf.core import Message, words
from squidpdf.documents.types import CountedSentence
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
        both = catalog.keys() & english.keys()
        unlike += [
            (language, key, "other placeholders")
            for key in both
            if placeholders(catalog[key]) != placeholders(english[key])
        ]
        unlike += [
            (language, key, "other kind")
            for key in both
            if _is_counted(catalog[key]) != _is_counted(english[key])
        ]
    return sorted(unlike)


def _is_counted(entry: words.CatalogEntry) -> bool:
    """Whether a sentence is said with a count, a form for each, rather than in one form."""
    return not isinstance(entry, str)


def test_every_language_has_every_sentence_with_the_same_placeholders_and_kind():
    """A sentence counted in one language and not in another would break every reply in it."""
    assert_equal(_sentences_unlike_english(), [], "sentences a language says unlike English")


@pytest.mark.parametrize(
    ("key", "said", "how"),
    [
        ("too_long", None, "missing"),
        ("too_long", "TOO LONG", "other placeholders"),
        ("too_long", "{share}% TOO LONG", "other placeholders"),
        ("not_a_sentence", "NOT A SENTENCE", "not in English"),
        ("back_to", {"one": "BACK TO “{text}”.", "other": "BACK TO “{text}”."}, "other kind"),
        ("find_places", "{count} PLACES, ", "other kind"),
    ],
    ids=[
        "a sentence left out",
        "a placeholder left out",
        "a placeholder renamed",
        "an extra",
        "a sentence counted",
        "a count in one form",
    ],
)
def test_a_language_unlike_english_is_found(pseudo, monkeypatch, key, said, how):
    """The check above finds the one thing changed in a language made from English."""
    if said is None:
        monkeypatch.delitem(words.CATALOGS[pseudo], key)
    else:
        monkeypatch.setitem(words.CATALOGS[pseudo], key, said)
    assert_equal(_sentences_unlike_english(), [(pseudo, key, how)], "what the check finds")


def _counted_unlike_the_wire() -> list[tuple[str, str]]:
    """Each sentence with a count a language gives other forms than the reply sends."""
    return sorted(
        (language, key)
        for language, catalog in words.CATALOGS.items()
        for key, entry in catalog.items()
        if not isinstance(entry, str) and entry.keys() != CountedSentence.__required_keys__
    )


def test_every_sentence_with_a_count_has_the_forms_the_reply_sends_in_every_language():
    """The reply sends each as `CountedSentence`: a form left out, or one more, has no place."""
    assert_equal(_counted_unlike_the_wire(), [], "sentences with a count, by language")


@pytest.mark.parametrize(
    "forms",
    [
        {"other": "{count} PLACES, "},
        {"one": "{count} PLACE, ", "few": "{count} PLACES, ", "other": "{count} PLACES, "},
    ],
    ids=["a form left out", "a form the reply hasn't"],
)
def test_a_sentence_with_a_count_unlike_the_reply_is_found(pseudo, monkeypatch, forms):
    """The check above finds the one sentence changed in a language made from English."""
    monkeypatch.setitem(words.CATALOGS[pseudo], "find_places", forms)
    found = _counted_unlike_the_wire()
    assert_equal(found, [(pseudo, "find_places")], "what the check finds")


def test_every_placeholder_is_bare_so_the_browser_can_fill_it_too():
    for language, catalog in words.CATALOGS.items():
        dressed = [
            key for key, entry in catalog.items() if any(map(_dressed, _forms_of(entry)))
        ]
        assert_equal(dressed, [], f"{language!r} sentences the browser can't fill")


def _forms_of(entry: words.CatalogEntry) -> list[str]:
    """A sentence's forms: itself alone, unless it takes a count."""
    return [entry] if isinstance(entry, str) else list(entry.values())


def _dressed(sentence: str) -> bool:
    """Whether a placeholder in `sentence` carries a format or a conversion, like `{n:.1f}`."""
    return any(
        name and (spec or conv) for _text, name, spec, conv in Formatter().parse(sentence)
    )


@pytest.mark.parametrize(
    ("message", "said"),
    [
        (
            Message("as_is_detail", {"delta_pt": 3.14159}),
            "Runs 3.1 pt past where the original ended.",
        ),
        # Rounded up from a half, as the browser's Intl.NumberFormat writes it too.
        (
            Message("as_is_detail", {"delta_pt": 2.25}),
            "Runs 2.3 pt past where the original ended.",
        ),
        (
            Message("as_is_detail", {"delta_pt": 1.45}),
            "Runs 1.5 pt past where the original ended.",
        ),
        (
            Message("missing", {"chars": ["é", "ß"], "font": "Carlito Bold"}),
            "no é or ß in this font, so the line is drawn in our Carlito Bold",
        ),
        (
            Message("left_out", {"letters": ["中", "文"]}),
            "Left out 中 文, which no font we have can draw.",
        ),
    ],
    ids=["a fraction", "a half", "a half just under in binary", "characters", "letters"],
)
def test_a_message_is_said_in_english_with_its_facts_written_out(message, said):
    assert_equal(words.render(message), said, "the sentence")


def test_a_number_is_written_with_the_reader_s_decimal_separator(pseudo, monkeypatch):
    monkeypatch.setitem(words.CATALOGS[pseudo], "decimal_separator", ",")
    said = words.render(Message("as_is_detail", {"delta_pt": 3.14159}), pseudo)
    expected = pseudo_sentence("Runs 3,1 pt past where the original ended.")
    assert_equal(said, expected, "a fraction in another language")


def test_a_list_with_no_joiner_of_its_own_is_joined_as_items():
    """Only `chars` and `letters` have their own; a new list must not fail when it's said."""
    fit = Message("missing", {"chars": ["é"], "font": ["Carlito Bold", "Arimo Regular"]})
    expected = "no é in this font, so the line is drawn in our Carlito Bold, Arimo Regular"
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
    expected = f"no é or {said} in this font, so the line is drawn in our Noto Sans Regular"
    assert_equal(words.render(fit), expected, "the sentence naming it")
    left_out = Message("left_out", {"letters": [character]})
    expected = f"Left out {said}, which no font we have can draw."
    assert_equal(words.render(left_out), expected, "the sentence naming it")


def test_punctuation_or_a_symbol_is_quoted_so_it_reads_as_a_character_and_a_letter_isnt():
    fit = Message("missing", {"chars": ["(", "Z", "€"], "font": "Noto Sans Regular"})
    expected = "no “(” or Z or “€” in this font, so the line is drawn in our Noto Sans Regular"
    assert_equal(words.render(fit), expected, "the sentence quoting what isn't a letter")


def test_a_letter_that_looks_like_another_is_named_so_the_two_read_apart():
    """In many faces capital I and lowercase l are one stroke, and O and 0 differ by width."""
    fit = Message("missing", {"chars": ["I", "l", "O", "0", "1"], "font": "Noto Sans Regular"})
    expected = (
        "no capital I or lowercase l or capital O or zero or 1 in this font,"
        " so the line is drawn in our Noto Sans Regular"
    )
    assert_equal(words.render(fit), expected, "each look-alike by name, and a 1 as itself")


def test_a_character_is_named_in_the_reader_s_language(pseudo):
    said = words.render(Message("left_out", {"letters": ["\u202f"]}), pseudo)
    expected = pseudo_sentence(
        "Left out narrow no-break space, which no font we have can draw."
    )
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
        != pseudo_sentence(f"Left out {said}, which no font we have can draw.")
    ]
    assert_equal(unsaid, [], "names in the catalog their character isn't said by")
