"""The app starts its workers, answers, and publishes its schema under /api."""

from __future__ import annotations

from fastapi.testclient import TestClient

from squidpdf.api.app import create_app
from tests.helpers import assert_equal, assert_in


def test_health_answers_once_the_app_has_started():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")
    assert_equal(response.status_code, 200, "health status")
    assert_equal(response.json(), {"status": "ok"}, "health body")


def test_the_schema_lives_under_api(browser):
    schema = browser().get("/api/openapi.json").json()
    assert_in("/api/health", schema["paths"], "paths in the schema")
