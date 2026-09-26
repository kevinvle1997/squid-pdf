"""Every failure is Problem Details, with a sentence a person can read."""

from __future__ import annotations

import pickle

from fastapi.testclient import TestClient

from squidpdf.api.errors import ServerError
from squidpdf.core import Damaged, Encrypted, NotFound, Problem, Unreadable, words
from squidpdf.documents.errors import Gone, TooManyPages
from squidpdf.editing.errors import BadReference
from tests.api.conftest import BASE_URL, upload
from tests.helpers import assert_equal, assert_not_in, assert_problem


def test_an_unknown_path_is_not_found(browser):
    assert_problem(browser().get("/api/nothing-here"), "not_found", 404)


def test_a_bad_request_is_one_plain_line_not_a_list(browser, pdf_bytes):
    mine = browser()
    doc = upload(mine, pdf_bytes).json()
    params = {"scale": 9, "build": doc["build"]}
    response = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params)
    assert_problem(response, "invalid_request", 400)
    assert_equal(response.json()["detail"], words.INVALID_REQUEST, "what the user reads")
    assert_equal(
        response.json()["debug"],
        "scale: Input should be less than or equal to 4",
        "what a developer reads",
    )


def test_a_crash_is_a_server_error_without_the_traceback(app):
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    response = client.get("/api/crash")
    assert_problem(response, "server_error", 500)
    assert_not_in("a bug nobody caught", response.text, "the body of a crash")


def _every(cls: type[Problem]) -> list[type[Problem]]:
    """`cls` and every subclass under it, however deep."""
    return [cls, *(sub for child in cls.__subclasses__() for sub in _every(child))]


def test_every_problem_has_its_own_wire_type(app):
    # `app` imports every feature, so every Problem subclass is defined by now.
    # The same failure, as far as the browser knows.
    shared = {Gone: NotFound, Damaged: Unreadable, ServerError: Problem}
    seen: dict[str, type[Problem]] = {}
    for cls in _every(Problem):
        if cls.__module__.startswith("tests."):
            continue
        assert_equal(cls.type, cls.type.lower(), f"{cls.__name__}.type is lower case")
        owner = seen.setdefault(cls.type, cls)
        if owner is not cls:
            assert_equal(shared.get(cls), owner, f"{cls.__name__} reuses {cls.type!r}")


def test_every_problem_says_the_english_catalog_s_sentence_under_its_type(app):
    # `app` imports every feature, so every Problem subclass is defined by now.
    for cls in _every(Problem):
        if cls.__module__.startswith("tests."):
            continue
        in_catalog = words.ENGLISH_SENTENCES.get(cls.type)
        assert_equal(in_catalog, cls.sentence, f"the catalog's sentence for {cls.__name__}")


def test_a_worker_s_problem_arrives_saying_the_same_thing():
    for raised in (TooManyPages(3), BadReference("s1"), Gone(), Encrypted(), Damaged()):
        back = pickle.loads(pickle.dumps(raised))
        assert_equal(type(back), type(raised), "the class after a pickle round trip")
        assert_equal(back.detail, raised.detail, f"what {type(raised).__name__} says")
