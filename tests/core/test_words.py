"""What the app says is kept as a Message, and put into a language only when it's said."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from string import Formatter

import pytest

from squidpdf.core import Message, words
from tests.helpers import assert_equal, assert_true

_SRC = Path(__file__).parents[2] / "src"


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


def test_every_placeholder_is_bare_so_the_browser_can_fill_it_too():
    for key, said in words.ENGLISH_SENTENCES.items():
        specs = [(spec, conv) for _t, name, spec, conv in Formatter().parse(said) if name]
        assert_true(all(spec == "" and conv is None for spec, conv in specs), f"{key}: {said}")


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


@pytest.mark.parametrize(
    ("character", "said"),
    [
        ("\u202f", "narrow no-break space"),
        ("\t", "U+0009"),
        ("\u0301", "combining acute accent"),
    ],
    ids=["one with a name", "one without", "an accent on its own: it'd sit on the space"],
)
def test_a_character_that_draws_nothing_is_named(character, said):
    fit = Message("missing", {"chars": ["é", character], "font": "Noto Sans Regular"})
    expected = f"no é or {said} in this font, so the line is drawn in Noto Sans Regular"
    assert_equal(words.render(fit), expected, "the sentence naming it")
    left_out = Message("left_out", {"letters": [character]})
    expected = f"Left out {said}: no font we have can draw them."
    assert_equal(words.render(left_out), expected, "the sentence naming it")


def test_every_key_the_code_names_is_in_the_catalog():
    """A mistyped key would pass every check and fail only when it's said."""
    named = {
        (key, str(path.relative_to(_SRC)))
        for path in sorted(_SRC.rglob("*.py"))
        for key in _sentence_keys(path)
    }
    unknown = sorted((key, path) for key, path in named if key not in words.ENGLISH_SENTENCES)
    assert_equal(unknown, [], "keys named in the code with no English sentence")


def _sentence_keys(path: Path) -> Iterator[str]:
    """Every key a file writes out as `Message("key", ...)`."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        # A call to Message whose first argument is a string written out.
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "Message" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield first.value
