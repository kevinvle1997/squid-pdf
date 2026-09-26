"""A Problem says the same thing wherever it's raised, a worker included."""

from __future__ import annotations

import pickle

from squidpdf.core import Damaged, Encrypted, Message, Problem, Unreadable, words
from squidpdf.documents.errors import TooLarge
from tests.conftest import pseudo_sentence
from tests.helpers import assert_equal, assert_true


class _Sized(Problem):
    """A problem whose own arguments differ from the base's."""

    type = "sized"
    sentence = "over {mb} MB"

    def __init__(self, mb: int) -> None:
        super().__init__(mb=mb)


def test_detail_is_the_sentence_filled():
    assert_equal(_Sized(100).detail, "over 100 MB", "the filled sentence")


def test_a_problem_survives_the_trip_from_a_worker():
    back = pickle.loads(pickle.dumps(_Sized(100)))
    assert_true(type(back) is _Sized, f"came back as {type(back).__name__}")
    assert_equal(back.detail, "over 100 MB", "the sentence after a pickle round trip")


def test_debug_survives_the_trip_too():
    back = pickle.loads(pickle.dumps(Problem(debug="why")))
    assert_equal(back.debug, "why", "debug after a pickle round trip")


def test_both_unreadable_kinds_are_unreadable_and_say_which():
    for kind, sentence in ((Encrypted, words.ENCRYPTED), (Damaged, words.DAMAGED)):
        assert_true(issubclass(kind, Unreadable), f"{kind.__name__} is Unreadable")
        assert_equal(kind().detail, sentence, f"what {kind.__name__} says")


def test_a_problem_is_a_message_its_type_the_key():
    assert_equal(TooLarge(100).message, Message("too_large", {"mb": 100}), "the message")


def test_a_problem_is_said_in_the_language_asked_for(pseudo):
    expected = pseudo_sentence(words.TOO_LARGE).format(mb=100)
    assert_equal(TooLarge(100).said_in(pseudo), expected, "the sentence in the pseudo-language")
    assert_equal(TooLarge(100).detail, "This file is over 100 MB.", "the sentence in English")


def test_a_problem_no_catalog_has_says_its_own_sentence_in_any_language(pseudo):
    assert_equal(_Sized(100).said_in(pseudo), "over 100 MB", "the class's own sentence")
