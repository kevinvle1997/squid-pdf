from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from squidpdf.api.rate import RecentStarts
from squidpdf.documents import store
from tests.api.conftest import NOT_A_PDF, ONE_ADDRESS, OTHER_ADDRESS, OwnApp, browser_on, upload
from tests.helpers import (
    assert_equal,
    assert_every_field_filled,
    assert_false,
    assert_problem,
    assert_true,
)

_MINUTE_S = 60.0


@pytest.fixture
def one_upload_a_minute(own_app: OwnApp) -> Iterator[TestClient]:
    """Its own: every other test uploads from the one test address."""
    with own_app.started(UPLOADS_PER_MINUTE=1) as server:
        yield server


def test_an_address_past_its_uploads_a_minute_is_told_to_wait_and_others_are_not(
    one_upload_a_minute,
):
    first = browser_on(one_upload_a_minute, client=(ONE_ADDRESS, 1))
    other = browser_on(one_upload_a_minute, client=(OTHER_ADDRESS, 1))
    assert_problem(upload(first, NOT_A_PDF), "not_a_pdf", 415)
    assert_problem(upload(first, b"%PDF-" + NOT_A_PDF), "rate_limited", 429)
    assert_problem(upload(other, NOT_A_PDF), "not_a_pdf", 415)
    assert_equal(os.listdir(store.root()), [], "documents on disk")


def test_an_upload_not_sent_as_a_pdf_is_refused_before_it_counts(one_upload_a_minute):
    browser = browser_on(one_upload_a_minute, client=(ONE_ADDRESS, 1))
    sent_as_text = browser.post(
        "/api/documents", content=NOT_A_PDF, headers={"content-type": "text/plain"}
    )
    assert_problem(sent_as_text, "not_sent_as_pdf", 415)
    assert_problem(upload(browser, NOT_A_PDF), "not_a_pdf", 415)


def test_an_ipv6_address_counts_with_the_rest_of_its_64(one_upload_a_minute):
    first, neighbour, elsewhere = (
        browser_on(one_upload_a_minute, client=(address, 1))
        for address in ("2001:db8:1:2::1", "2001:db8:1:2::ff", "2001:db8:1:3::1")
    )
    assert_problem(upload(first, NOT_A_PDF), "not_a_pdf", 415)
    assert_problem(upload(neighbour, NOT_A_PDF), "rate_limited", 429)
    assert_problem(upload(elsewhere, NOT_A_PDF), "not_a_pdf", 415)
    # IPv4 written as IPv6, as a server listening on both sees it: by its own address.
    for address in (f"::ffff:{ONE_ADDRESS}", f"::ffff:{OTHER_ADDRESS}"):
        browser = browser_on(one_upload_a_minute, client=(address, 1))
        assert_problem(upload(browser, NOT_A_PDF), "not_a_pdf", 415)


def test_an_address_may_upload_again_a_minute_after_its_first():
    recent = RecentStarts()
    assert_true(recent.admit(ONE_ADDRESS, now=0.0, limit=2), "the first upload")
    assert_true(recent.admit(ONE_ADDRESS, now=30.0, limit=2), "the second, half a minute on")
    assert_false(recent.admit(ONE_ADDRESS, now=59.0, limit=2), "a third within the minute")
    assert_true(
        recent.admit(ONE_ADDRESS, now=_MINUTE_S + 1, limit=2), "a minute after the first"
    )
    assert_false(
        recent.admit(ONE_ADDRESS, now=_MINUTE_S + 2, limit=2), "another, with two this minute"
    )


def test_a_record_made_with_its_own_window_counts_a_start_that_long():
    recent = RecentStarts(window_s=15 * _MINUTE_S)
    assert_true(recent.admit(ONE_ADDRESS, now=0.0, limit=1), "the first")
    assert_false(
        recent.admit(ONE_ADDRESS, now=14 * _MINUTE_S, limit=1), "another, well past a minute"
    )
    assert_true(
        recent.admit(ONE_ADDRESS, now=15 * _MINUTE_S + 1, limit=1), "once the window has passed"
    )


def test_addresses_quiet_for_a_minute_are_forgotten():
    recent = RecentStarts()
    for n in range(100):
        recent.admit(f"192.0.2.{n}", now=0.0, limit=1)
    recent.admit(OTHER_ADDRESS, now=2 * _MINUTE_S, limit=1)
    assert_equal(list(recent.started), [OTHER_ADDRESS], "addresses still counted")


def test_uploads_that_forget_quiet_addresses_equal_fresh_ones_but_for_the_clock():
    recent = RecentStarts(window_s=2 * _MINUTE_S)
    recent.admit(ONE_ADDRESS, now=3 * _MINUTE_S, limit=1)
    assert_every_field_filled(recent, RecentStarts(), "the record once one upload is counted")

    recent.forget_quiet(5 * _MINUTE_S)

    fresh = RecentStarts(window_s=2 * _MINUTE_S)
    as_if_fresh = replace(recent, forgotten_at=fresh.forgotten_at)
    assert_equal(as_if_fresh, fresh, "the record once it forgot, its clock aside")
