"""Each address may start so many uploads a minute; past that, it's told to wait."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from squidpdf.api import constants as limits
from squidpdf.api.app import create_app
from squidpdf.api.rate import RecentUploads
from squidpdf.documents import store
from tests.api.conftest import browser_on, upload
from tests.helpers import (
    assert_equal,
    assert_every_field_filled,
    assert_false,
    assert_problem,
    assert_true,
)

# Refused as not a PDF before any work: the count is all these tests look at.
_NOT_A_PDF = b"Dear Sir, please find attached."
_ONE = "192.0.2.1"  # addresses kept for documentation, so nobody's real one
_OTHER = "198.51.100.7"
_MINUTE_S = 60.0


@pytest.fixture
def one_upload_a_minute(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """An app of its own, started, taking one upload a minute from each address.

    Its own: every other test uploads from the one test address.
    """
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    monkeypatch.setattr(limits, "UPLOADS_PER_MINUTE", 1)
    with TestClient(create_app()) as server:  # runs the lifespan: the pool and the sweeper
        yield server


def test_an_address_past_its_uploads_a_minute_is_told_to_wait_and_others_are_not(
    one_upload_a_minute,
):
    first = browser_on(one_upload_a_minute, client=(_ONE, 1))
    other = browser_on(one_upload_a_minute, client=(_OTHER, 1))
    assert_problem(upload(first, _NOT_A_PDF), "not_a_pdf", 415)
    assert_problem(upload(first, b"%PDF-" + _NOT_A_PDF), "rate_limited", 429)
    assert_problem(upload(other, _NOT_A_PDF), "not_a_pdf", 415)
    assert_equal(os.listdir(store.root()), [], "documents on disk")


def test_an_upload_not_sent_as_a_pdf_is_refused_before_it_counts(one_upload_a_minute):
    """Another site's page can send one without asking: counted, it would use up the minute."""
    browser = browser_on(one_upload_a_minute, client=(_ONE, 1))
    sent_as_text = browser.post(
        "/api/documents", content=_NOT_A_PDF, headers={"content-type": "text/plain"}
    )
    assert_problem(sent_as_text, "not_sent_as_pdf", 415)
    # Admitted, and refused only for what it holds.
    assert_problem(upload(browser, _NOT_A_PDF), "not_a_pdf", 415)


def test_an_ipv6_address_counts_with_the_rest_of_its_64(one_upload_a_minute):
    """One home or machine gets a whole /64: counted apart, each address could flood."""
    first, neighbour, elsewhere = (
        browser_on(one_upload_a_minute, client=(address, 1))
        # Addresses kept for documentation, as above: two in one /64, one in the next.
        for address in ("2001:db8:1:2::1", "2001:db8:1:2::ff", "2001:db8:1:3::1")
    )
    assert_problem(upload(first, _NOT_A_PDF), "not_a_pdf", 415)
    assert_problem(upload(neighbour, _NOT_A_PDF), "rate_limited", 429)
    assert_problem(upload(elsewhere, _NOT_A_PDF), "not_a_pdf", 415)
    # IPv4 written as IPv6, as a server listening on both sees it: by its own address.
    for address in (f"::ffff:{_ONE}", f"::ffff:{_OTHER}"):
        browser = browser_on(one_upload_a_minute, client=(address, 1))
        assert_problem(upload(browser, _NOT_A_PDF), "not_a_pdf", 415)


def test_an_address_may_upload_again_a_minute_after_its_first():
    recent = RecentUploads()
    assert_true(recent.admit(_ONE, now=0.0, limit=2), "the first upload")
    assert_true(recent.admit(_ONE, now=30.0, limit=2), "the second, half a minute on")
    assert_false(recent.admit(_ONE, now=59.0, limit=2), "a third within the minute")
    assert_true(recent.admit(_ONE, now=_MINUTE_S + 1, limit=2), "a minute after the first")
    assert_false(
        recent.admit(_ONE, now=_MINUTE_S + 2, limit=2), "another, with two this minute"
    )


def test_addresses_quiet_for_a_minute_are_forgotten():
    """A flood from many addresses leaves nothing behind once it stops."""
    recent = RecentUploads()
    for n in range(100):
        recent.admit(f"192.0.2.{n}", now=0.0, limit=1)
    recent.admit(_OTHER, now=2 * _MINUTE_S, limit=1)
    assert_equal(list(recent.started), [_OTHER], "addresses still counted")


def test_uploads_that_forget_quiet_addresses_equal_fresh_ones_but_for_the_clock():
    """A forget notes when it ran, so that alone differs from a fresh record."""
    recent = RecentUploads()
    recent.admit(_ONE, now=2 * _MINUTE_S, limit=1)
    assert_every_field_filled(recent, RecentUploads(), "the record once one upload is counted")

    recent.forget_quiet(4 * _MINUTE_S)

    fresh = RecentUploads()
    as_if_fresh = replace(recent, forgotten_at=fresh.forgotten_at)
    assert_equal(as_if_fresh, fresh, "the record once it forgot, its clock aside")
