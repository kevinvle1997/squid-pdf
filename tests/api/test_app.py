"""The app starts its workers, answers, and publishes its schema under /api."""

from __future__ import annotations

from fastapi.testclient import TestClient

from squidpdf.api import constants as limits
from squidpdf.api.app import create_app
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_problem


def test_health_answers_once_the_app_has_started():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert_equal(response.status_code, 200, "health status")
    assert_equal(response.json(), {"status": "ok"}, "health body")


def test_the_schema_lives_under_api(browser):
    schema = browser().get("/api/openapi.json").json()
    assert_in("/api/health", schema["paths"], "paths in the schema")


def test_an_edit_list_too_large_is_refused_but_an_upload_that_size_is_not(
    browser, pdf_bytes, monkeypatch
):
    """Uploads check their own, larger limit as they stream in."""
    monkeypatch.setattr(limits, "MAX_BODY_BYTES", len(pdf_bytes) // 2)
    mine = browser()

    uploaded = upload(mine, pdf_bytes)
    regions = [{"page": 0}] * len(pdf_bytes)  # far past the limit once written out
    body = {"edits": [], "scale": 1, "regions": regions}
    too_large = mine.post(f"/api/documents/{uploaded.json()['id']}/render", json=body)

    assert_equal(uploaded.status_code, 201, f"status of an upload of {len(pdf_bytes)} bytes")
    assert_problem(too_large, "request_too_large", 413)
