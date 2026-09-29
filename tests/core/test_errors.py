"""A Problem says the same thing wherever it's raised, a worker included."""

from __future__ import annotations

import pickle

from squidpdf.core import Problem
from tests.helpers import assert_equal, assert_true


class _Sized(Problem):
    """A problem whose own arguments differ from the base's."""

    type = "sized"
    sentence = "over {mb} MB"

    def __init__(self, mb: int) -> None:
        super().__init__(mb=mb)


def test_a_problem_survives_the_trip_from_a_worker():
    """The API tests' refusals take no arguments; one that does must still unpickle."""
    back = pickle.loads(pickle.dumps(_Sized(100)))
    assert_true(type(back) is _Sized, f"came back as {type(back).__name__}")
    assert_equal(back.detail, "over 100 MB", "the sentence after a pickle round trip")
