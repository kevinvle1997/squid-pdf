"""Every failure is Problem Details, with a sentence a person can read."""

from __future__ import annotations

from fastapi.testclient import TestClient

from squidpdf.core import words
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
