"""Every failure is Problem Details, with a sentence a person can read."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from squidpdf.api.errors import ServerError
from squidpdf.core import NotFound, Problem, words
from squidpdf.documents.errors import Gone
from tests.api.conftest import BASE_URL, SERVER_PATH, upload
from tests.helpers import assert_equal, assert_in, assert_not_in, assert_problem


def test_an_unknown_path_is_not_found(browser):
    assert_problem(browser().get("/api/nothing-here"), "not_found", 404)


def test_a_method_a_path_doesnt_take_says_which_it_does(browser):
    response = browser().put("/api/documents")
    assert_problem(response, "method_not_allowed", 405)
    assert_equal(response.headers.get("allow"), "POST", "the methods it takes")


def test_a_bad_request_is_one_plain_line_not_a_list(browser, pdf_bytes):
    mine = browser()
    doc = upload(mine, pdf_bytes).json()
    params = {"scale": 9, "build": doc["build"]}
    response = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params)
    assert_problem(response, "invalid_request", 400)
    assert_equal(
        response.json()["detail"], words.sentence("invalid_request"), "what the user reads"
    )
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


def test_a_server_error_logs_its_debug_and_never_sends_it(browser, caplog):
    """A 5xx's debug can name a file on the server; a 4xx's keeps it (the bad request above)."""
    with caplog.at_level(logging.WARNING, logger="squidpdf.api"):
        response = browser().get("/api/crash-naming-a-path")
    assert_problem(response, "server_error", 500)
    assert_not_in("debug", response.json(), "what a server error sends")
    assert_not_in(SERVER_PATH, response.text, "the body of a server error")
    assert_in(SERVER_PATH, caplog.text, "what the server's log says of it")
    assert_in("server_error", caplog.text, "the type the log gives it")


def _every(cls: type[Problem]) -> list[type[Problem]]:
    """`cls` and every subclass under it, however deep."""
    return [cls, *(sub for child in cls.__subclasses__() for sub in _every(child))]


def _ours() -> list[type[Problem]]:
    """Every Problem the app defines: not a test's own, or a library's."""
    # `create_app`'s module imports every feature, so every Problem subclass is defined by now.
    return [cls for cls in _every(Problem) if cls.__module__.startswith("squidpdf.")]


def _spec() -> dict:
    """The OpenAPI the browser's types are written from.

    A fresh app, not the fixture's: that one has a test-only route.
    """
    return TestClient(create_app(), base_url=BASE_URL).get("/api/openapi.json").json()


def test_every_problem_has_its_own_wire_type_and_an_english_sentence():
    """The browser branches on the type; one with no sentence fails in the error handler."""
    # The same failure, as far as the browser knows.
    shared = {Gone: NotFound, ServerError: Problem}
    seen: dict[str, type[Problem]] = {}
    for cls in _ours():
        said = f"the catalog's sentence for {cls.__name__}"
        assert_in(cls.type, words.CATALOGS[words.ENGLISH], said)
        owner = seen.setdefault(cls.type, cls)
        if owner is not cls:
            assert_equal(shared.get(cls), owner, f"{cls.__name__} reuses {cls.type!r}")


def test_every_problem_type_is_in_the_openapi_so_the_browser_can_branch_on_it():
    listed = _spec()["components"]["schemas"]["ProblemInfo"]["properties"]["type"]["enum"]
    for cls in _ours():
        assert_in(cls.type, listed, f"the wire types the browser knows, for {cls.__name__}")


def test_every_route_says_it_can_answer_with_a_problem():
    # The browser's types come from the OpenAPI, so a Problem must be in it.
    spec = _spec()
    problem = spec["components"]["schemas"]["ProblemInfo"]
    assert_equal(
        sorted(problem["required"]),
        ["code", "detail", "params", "status", "type"],
        "what every Problem carries",
    )
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            listed = operation["responses"].get("default", {})
            schema = listed.get("content", {}).get("application/problem+json", {}).get("schema")
            assert_equal(
                schema,
                {"$ref": "#/components/schemas/ProblemInfo"},
                f"{method.upper()} {path} answers a Problem",
            )
            assert_not_in("422", operation["responses"], f"{method.upper()} {path}'s 422")
