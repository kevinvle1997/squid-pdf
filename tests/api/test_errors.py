"""Every failure is Problem Details, with a sentence a person can read."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.conftest import BASE_URL
from tests.helpers import assert_equal, assert_not_in, assert_problem


def test_an_unknown_path_is_not_found(browser):
    assert_problem(browser().get("/api/nothing-here"), "not_found", 404)


def test_a_bad_request_is_one_plain_line_not_a_list(browser):
    response = browser().get("/api/things/some-id", params={"scale": "big"})
    assert_problem(response, "invalid_request", 400)
    assert_equal(
        response.json()["detail"],
        "Something in the request isn't right "
        "(scale: Input should be a valid integer, unable to parse string as an integer).",
        "the invalid request sentence",
    )


def test_a_crash_is_a_server_error_without_the_traceback(app):
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    response = client.get("/api/crash")
    assert_problem(response, "server_error", 500)
    assert_not_in("a bug nobody caught", response.text, "the body of a crash")
