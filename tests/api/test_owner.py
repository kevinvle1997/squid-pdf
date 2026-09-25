"""A document answers to the browser that uploaded it. To anyone else it doesn't exist."""

from __future__ import annotations

import pytest

from tests.api.conftest import upload
from tests.helpers import assert_all, assert_equal, assert_in, assert_problem

_UNKNOWN = "A" * 22  # shaped like an id, belongs to nothing


@pytest.mark.parametrize("doc_id", [_UNKNOWN, "..%2F..%2Fetc"])
def test_an_unknown_id_is_not_found(browser, doc_id):
    assert_problem(browser().get(f"/api/documents/{doc_id}"), "not_found", 404)


def test_another_browsers_document_is_not_found_exactly_like_an_unknown_one(browser, pdf_bytes):
    mine, theirs = browser(), browser()
    doc = upload(mine, pdf_bytes).json()
    base = f"/api/documents/{doc['id']}"

    attempts = [
        theirs.get(base),
        theirs.get(f"{base}/pages/0", params={"scale": 1, "build": doc["build"]}),
        theirs.delete(base),
    ]
    upload(theirs, pdf_bytes)  # now it has a cookie of its own
    attempts.append(theirs.get(base))
    unknown = theirs.get(f"/api/documents/{_UNKNOWN}")

    for attempt in attempts:
        assert_problem(attempt, "not_found", 404)
    assert_equal(attempts[-1].json(), unknown.json(), "someone else's id beside an unknown one")
    assert_equal(mine.get(base).status_code, 200, "the uploader reading it after all that")


def test_one_browser_owns_everything_it_uploads(browser, pdf_bytes):
    mine = browser()
    ids = [upload(mine, pdf_bytes).json()["id"] for _ in range(2)]
    assert_all(ids, lambda i: mine.get(f"/api/documents/{i}").status_code == 200)


def test_the_cookie_is_hidden_from_scripts_and_other_sites(browser, pdf_bytes):
    cookie = upload(browser(), pdf_bytes).headers["set-cookie"]
    for attribute in ("__Host-owner=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert_in(attribute, cookie, "the owner cookie")
