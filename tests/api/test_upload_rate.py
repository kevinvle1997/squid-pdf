"""Each address may start so many uploads a minute; past that, it's told to wait."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from squidpdf.api import constants as limits
from squidpdf.api.app import create_app
from squidpdf.api.rate import RecentUploads
from squidpdf.documents import store
from tests.api.conftest import BASE_URL, upload
from tests.helpers import assert_equal, assert_false, assert_problem, assert_true

# Refused as not a PDF before any work: the count is all these tests look at.
_NOT_A_PDF = b"Dear Sir, please find attached."
_ONE = "192.0.2.1"  # addresses kept for documentation, so nobody's real one
_OTHER = "198.51.100.7"
_MINUTE_S = 60.0


def test_an_address_past_its_uploads_a_minute_is_told_to_wait_and_others_are_not(
    tmp_path, monkeypatch
):
    """On an app of its own: every other test uploads from the one test address."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path))
    monkeypatch.setattr(limits, "UPLOADS_PER_MINUTE", 1)
    app = create_app()
    with TestClient(app):  # runs the lifespan: the pool and the sweeper
        first = TestClient(app, base_url=BASE_URL, client=(_ONE, 1))
        other = TestClient(app, base_url=BASE_URL, client=(_OTHER, 1))
        assert_problem(upload(first, _NOT_A_PDF), "not_a_pdf", 415)
        assert_problem(upload(first, b"%PDF-" + _NOT_A_PDF), "rate_limited", 429)
        assert_problem(upload(other, _NOT_A_PDF), "not_a_pdf", 415)
    assert_equal(os.listdir(store.root()), [], "documents on disk")


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
