"""The app starts its workers, answers, and publishes its schema under /api."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

import pytest

from squidpdf.api import constants as limits
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.documents.store import FolderStore
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_problem


def test_health_answers_once_the_app_has_started(browser):
    response = browser().get("/api/health")
    assert_equal(response.status_code, 200, "health status")
    assert_equal(response.json(), {"status": "ok"}, "health body")


@pytest.mark.parametrize("sent", ["with its size", "in chunks, size unsaid"])
def test_an_edit_list_too_large_is_refused_but_an_upload_that_size_is_not(
    browser, pdf_bytes, monkeypatch, sent
):
    """Uploads check their own, larger limit as they stream in.

    Sent in chunks, a body states no size up front: it's counted as it comes.
    """
    monkeypatch.setattr(limits, "MAX_BODY_BYTES", len(pdf_bytes) // 2)
    mine = browser()

    uploaded = upload(mine, pdf_bytes)
    regions = [{"page": 0}] * len(pdf_bytes)  # far past the limit once written out
    body = json.dumps({"edits": [], "scale": 1, "regions": regions}).encode()
    content = body if sent == "with its size" else iter([body[:1024], body[1024:]])
    url = f"/api/documents/{uploaded.json()['id']}/render"
    too_large = mine.post(url, content=content, headers={"Content-Type": "application/json"})

    assert_equal(uploaded.status_code, 201, f"status of an upload of {len(pdf_bytes)} bytes")
    assert_problem(too_large, "request_too_large", 413)


def _opened_noting(path: str, *, log: Path) -> PdfDriver:
    """MuPDF's driver for `path`, the file's name noted in `log`, which every worker shares."""
    with log.open("a") as noted:
        noted.write(f"{Path(path).name}\n")
    return open_driver(path)


def test_every_document_is_opened_in_the_driver_the_apps_store_is_given(
    app, browser, pdf_bytes, monkeypatch, tmp_path
):
    """The driver is chosen once, in the app's store, and every request opens through it.

    The store crosses to the workers with each job. Each request's opens are counted exactly,
    so one that slipped back to the default driver would show as one fewer.
    """
    log = tmp_path / "opened"
    monkeypatch.setattr(app.state, "documents", FolderStore(partial(_opened_noting, log=log)))
    mine = browser()
    seen = 0

    def opened_since() -> list[str]:
        """The files the given driver opened since the last ask."""
        nonlocal seen
        lines = log.read_text().split() if log.exists() else []
        new, seen = lines[seen:], len(lines)
        return new

    doc = upload(mine, pdf_bytes).json()
    # Taken in (what acts on its own is looked for), then analysed.
    by_upload = opened_since()
    image = mine.get(
        f"/api/documents/{doc['id']}/pages/0", params={"scale": 2, "build": doc["build"]}
    )
    by_page_image = opened_since()
    body = {"edits": [], "scale": 1, "regions": [{"page": 0}]}
    rendered = mine.post(f"/api/documents/{doc['id']}/render", json=body)
    by_render = opened_since()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": []})
    # The original, then the saved file, read again for the redaction check.
    by_export = opened_since()

    statuses = (image.status_code, rendered.status_code, exported.status_code)
    assert_equal(statuses, (200, 200, 200), "the page image's, render's and export's status")
    assert_equal(by_upload, ["original.pdf", "original.pdf"], "files the upload opened")
    assert_equal(by_page_image, ["original.pdf"], "files the page image opened")
    assert_equal(by_render, ["original.pdf"], "files render opened")
    assert_equal(by_export, ["original.pdf", "export.pdf"], "files export opened")
