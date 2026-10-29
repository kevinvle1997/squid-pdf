"""The real app with its workers running, one for the whole run, and browsers to call it."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pymupdf
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from squidpdf.api import constants as api_constants
from squidpdf.api.app import create_app
from squidpdf.core import Message, Problem
from squidpdf.core.pdf.driver import DriverError, PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import FontResource
from tests.conftest import log_lines
from tests.helpers import assert_equal

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"
_MARGIN_PT = 4  # above and below a line, as the browser pads its strip


# A document's file on the server, as MuPDF names one it couldn't open, and as the log
# writes it: the folder is the document's id, which the log never says.
DOCUMENT_ID = "q3Xy-9Lk2_vB7mWnR4tPzA"
SERVER_PATH = f"/data/{DOCUMENT_ID}/original.pdf"
LOGGED_PATH = "/data/<document>/original.pdf"


def _crashes() -> APIRouter:
    """Routes that fail on the server's side, to see what a failure looks like from outside."""
    router = APIRouter()

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    @router.get("/api/crash-naming-a-path")
    def crash_naming_a_path() -> None:
        # How core reports MuPDF failing to open a file.
        raise Problem(debug=f"FzErrorSystem: cannot open {SERVER_PATH}: No such file")

    return router


# Every test sends from one address, far more, and more at once, than a browser may: plenty
# for the suite.
_SUITE_UPLOADS_PER_MINUTE = 10_000
_SUITE_AT_ONCE = 1_000


@pytest.fixture(scope="session")
def session_app(tmp_path_factory) -> Iterator[FastAPI]:
    """One app for the whole run, workers started once: a pool's first task costs a second.

    Under xdist each worker is its own session, so each has one. A test that kills a
    worker or changes the app for good builds its own pool or app, never this one.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        # The per-address limits read these when each request comes in.
        env.setattr(api_constants, "UPLOADS_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        env.setattr(api_constants, "UPLOADS_UNDER_WAY", _SUITE_AT_ONCE)
        env.setattr(api_constants, "JOBS_AT_WORK", _SUITE_AT_ONCE)
        app = create_app()
        app.include_router(_crashes())
        yield app


@pytest.fixture(scope="session")
def server(session_app: FastAPI) -> Iterator[TestClient]:
    """The run's app started, its pool and sweeper with it, in the loop every browser shares."""
    with TestClient(session_app) as server:  # runs the lifespan
        yield server


@pytest.fixture(scope="module")
def app(session_app: FastAPI, server: TestClient, tmp_path_factory) -> Iterator[FastAPI]:
    """The run's app, started, with this module's documents kept in a fresh folder of their own.

    The folder is read on each request, and work goes to the pool by absolute path,
    so the workers started for an earlier module find this one's documents.
    """
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        yield session_app


def browser_on(server: TestClient, **options: Any) -> TestClient:
    """A new browser on `server`'s app, with cookies of its own; `options` go to TestClient.

    Its requests run in the loop `server` started the app in, as a server runs
    one: the pool works only in the loop it was made in, and a TestClient on its
    own gives each request a new loop.
    """
    browser = TestClient(server.app, base_url=BASE_URL, **options)
    browser.portal = server.portal
    return browser


@pytest.fixture
def browser(app: FastAPI, server: TestClient) -> Callable[[], TestClient]:
    """Opens a new browser on the app each call; each keeps its own cookies."""
    return lambda: browser_on(server)


class InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run(self, _timeout, task):
        return task()


# The facts of `with_a_card_line`: its one line, and its two numbers, by code point.
CARD_LINE = "Card 4421 then 9918 ends"
FIRST_NUMBER = range(5, 9)
SECOND_NUMBER = range(15, 19)


def with_a_card_line() -> bytes:
    """A page whose one line holds two numbers, its title the line's words: a hidden copy."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), CARD_LINE, fontname="helv", fontsize=12)
    doc.set_metadata({"title": CARD_LINE})
    return doc.tobytes()


def drawn_page(drawing: str, *, title: str = "", link: tuple[str, str] | None = None) -> bytes:
    """A page whose drawing is `drawing`, its font Helvetica as `/F`, with `title` its title.

    `link` is an address, and the words its link sits over. Written by hand, to draw text as
    some files do: in pieces, spaced out, in marked content, or twice over.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    # A font for the drawing to name: PyMuPDF adds one with the text it writes, off the page.
    page.insert_text((0, -100), "x", fontname="helv", fontsize=1)
    [(*_, resource, _referencer)] = page.get_fonts()
    doc.update_stream(page.get_contents()[0], drawing.replace("/F ", f"/{resource} ").encode())
    if link is not None:
        address, over = link
        [box] = page.search_for(over)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": box, "uri": address})
    doc.set_metadata({"title": title})
    return doc.tobytes()


def upload(client: TestClient, body: bytes) -> Response:
    """Send a file the way the browser does: the raw bytes, no form."""
    return client.post(
        "/api/documents", content=body, headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def pdf_bytes(pdf: str) -> bytes:
    """The shared sample PDF, as a browser would upload it."""
    return Path(pdf).read_bytes()


@pytest.fixture
def mine(browser: Callable[[], TestClient]) -> TestClient:
    """The browser that uploads, and so owns, the document."""
    return browser()


@pytest.fixture
def doc(mine: TestClient, pdf_bytes: bytes) -> dict:
    """The sample PDF as uploaded by `mine`: what the upload answered."""
    return upload(mine, pdf_bytes).json()


def span_starting(doc: dict, page: int, starts: str) -> dict:
    """The span on `page` of an uploaded `doc` whose text starts with `starts`."""
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def around(span: dict) -> dict:
    """A region: the full-width strip over a span's line."""
    box = span["bbox"]
    return {"page": span["page"], "y0": box["y0"] - _MARGIN_PT, "y1": box["y1"] + _MARGIN_PT}


def log_fields(line: str) -> dict[str, str]:
    """A line's fields, by name."""
    return dict(word.split("=", 1) for word in line.split() if "=" in word)


def export_lines(mine, doc: dict, edits: list[dict], capsys) -> list[str]:
    """Export `edits`, and the lines it logged."""
    capsys.readouterr()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal(exported.status_code, 200, "exported")
    return log_lines(capsys.readouterr().out)


def edit_counts(lines: list[str]) -> dict[str, str]:
    """The export's counts of its edits by fidelity, as its access line has them."""
    [access] = [line for line in lines if " request_done " in line]
    fields = log_fields(access)
    return {name: count for name, count in fields.items() if name.startswith("edits_")}


@dataclass(frozen=True, slots=True)
class _RefusingDriver:
    """MuPDF's driver, on pages that refuse the file's own fonts' copies, as broken ones do."""

    driver: PdfDriver

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """Refused for the file's own copies, which the writer names "F…"; else added."""
        if resource.startswith("F"):
            raise DriverError(Message("font_not_added"))
        return self.driver.add_font(page, font_file, resource=resource)

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the real driver answers it."""
        return getattr(self.driver, name)


def refusing_own_copies(path: str) -> PdfDriver:
    """A driver factory for the store: MuPDF's, on pages that refuse the file's own copies."""
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _RefusingDriver(open_driver(path)))
