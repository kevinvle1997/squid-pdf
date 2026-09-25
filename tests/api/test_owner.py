"""A document answers to the browser that uploaded it. To anyone else it doesn't exist."""

from __future__ import annotations

from tests.helpers import assert_all, assert_equal, assert_in, assert_problem


def test_an_unknown_id_is_not_found(browser):
    assert_problem(browser().get("/api/things/no-such-id"), "not_found", 404)


def test_another_browsers_id_is_not_found_exactly_like_an_unknown_one(browser):
    mine, theirs = browser(), browser()
    thing = mine.post("/api/things").json()["id"]
    assert_equal(mine.get(f"/api/things/{thing}").status_code, 200, "the uploader reading it")

    without_cookie = theirs.get(f"/api/things/{thing}")
    theirs.post("/api/things")
    with_own_cookie = theirs.get(f"/api/things/{thing}")
    unknown = theirs.get("/api/things/no-such-id")

    assert_problem(without_cookie, "not_found", 404)
    assert_problem(with_own_cookie, "not_found", 404)
    assert_equal(
        with_own_cookie.json(), unknown.json(), "someone else's id beside an unknown one"
    )


def test_one_browser_owns_everything_it_uploads(browser):
    mine = browser()
    things = [mine.post("/api/things").json()["id"] for _ in range(2)]
    assert_all(things, lambda t: mine.get(f"/api/things/{t}").status_code == 200)


def test_the_cookie_is_hidden_from_scripts_and_other_sites(browser):
    cookie = browser().post("/api/things").headers["set-cookie"]
    for attribute in ("__Host-owner=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert_in(attribute, cookie, "the owner cookie")
