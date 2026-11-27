from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
from squidpdf.core.types import DrawnGlyph, FontResource, PlacedLetter, Rect
from tests.conftest import log_lines
from tests.helpers import assert_equal

# The owner cookie is Secure, so a browser only sends it back over https.
BASE_URL = "https://testserver"
_MARGIN_PT = 4
_RENDER_SCALE = 2

NOT_A_PDF = b"Dear Sir, please find attached."
# Addresses kept for documentation, so nobody's real one.
ONE_ADDRESS = "192.0.2.1"
OTHER_ADDRESS = "198.51.100.7"


# As MuPDF names a file it couldn't open; its folder is the id, which the log never says.
DOCUMENT_ID = "q3Xy-9Lk2_vB7mWnR4tPzA"
SERVER_PATH = f"/data/{DOCUMENT_ID}/original.pdf"
LOGGED_PATH = "/data/<document>/original.pdf"


def _crashes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/crash")
    def crash() -> None:
        raise RuntimeError("a bug nobody caught")

    @router.get("/api/crash-naming-a-path")
    def crash_naming_a_path() -> None:
        raise Problem(debug=f"FzErrorSystem: cannot open {SERVER_PATH}: No such file")

    return router


# Far more, and more at once, than a browser may: plenty for the suite.
_SUITE_UPLOADS_PER_MINUTE = 10_000
_SUITE_AT_ONCE = 1_000


@pytest.fixture(scope="session")
def session_app(tmp_path_factory) -> Iterator[FastAPI]:
    """One app for the whole run, workers started once: a pool's first task costs a second."""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        # The per-address limits read these when each request comes in.
        env.setattr(api_constants, "UPLOADS_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        env.setattr(api_constants, "UPLOADS_UNDER_WAY", _SUITE_AT_ONCE)
        env.setattr(api_constants, "JOBS_AT_WORK", _SUITE_AT_ONCE)
        env.setattr(api_constants, "LINKS_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        env.setattr(api_constants, "LINKS_PER_INBOX", _SUITE_UPLOADS_PER_MINUTE)
        env.setattr(api_constants, "PAYMENT_PAGES_PER_MINUTE", _SUITE_UPLOADS_PER_MINUTE)
        app = create_app()
        app.include_router(_crashes())
        yield app


@pytest.fixture(scope="session")
def server(session_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(session_app) as server:
        yield server


@pytest.fixture(scope="module")
def app(session_app: FastAPI, server: TestClient, tmp_path_factory) -> Iterator[FastAPI]:
    """The folder is read on each request, so workers started for an earlier module find it."""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("SQUIDPDF_DATA", str(tmp_path_factory.mktemp("data")))
        yield session_app


def browser_on(server: TestClient, **options: Any) -> TestClient:
    """Requests run in `server`'s loop: the pool works only in the loop it was made in."""
    browser = TestClient(server.app, base_url=BASE_URL, **options)
    browser.portal = server.portal
    return browser


@pytest.fixture
def browser(app: FastAPI, server: TestClient) -> Callable[[], TestClient]:
    return lambda: browser_on(server)


@dataclass(frozen=True, slots=True, eq=False)
class OwnApp:
    """For a test that breaks the pool or tests a limit, which the shared app raises."""

    app: FastAPI
    monkeypatch: pytest.MonkeyPatch

    @contextmanager
    def started(self, **limits: int) -> Iterator[TestClient]:
        for name, value in limits.items():
            self.monkeypatch.setattr(api_constants, name, value)
        with TestClient(self.app, base_url=BASE_URL) as server:
            yield server


@pytest.fixture
def own_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> OwnApp:
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "data"))
    return OwnApp(create_app(), monkeypatch)


class InProcess:
    """Runs pool work in the test's own process, where a monkeypatch reaches it."""

    async def run[T](self, _timeout: float, task: Callable[[], T]) -> T:
        return task()


type Place = tuple[float, float]


@dataclass(slots=True)
class _MovingDriver:
    """As MuPDF's rewrite of a page can set a line further along, or elsewhere."""

    driver: PdfDriver
    moves: Callable[[Place], Place]
    when: Callable[[Rect], bool]
    erased: bool

    def erase_text(self, page: int, boxes: list[Rect]) -> list[str]:
        self.erased = self.erased or any(self.when(box) for box in boxes)
        return self.driver.erase_text(page, boxes)

    def drawn_glyphs(self, page: int) -> list[DrawnGlyph]:
        glyphs = self.driver.drawn_glyphs(page)
        return [replace(glyph, origin=self._moved(glyph.origin)) for glyph in glyphs]

    def drawn_glyph_boxes(self, page: int) -> list[Rect] | None:
        glyphs = self.driver.drawn_glyphs(page)
        boxes = self.driver.drawn_glyph_boxes(page)
        if boxes is None:
            return None
        return [
            _set_off(box, glyph.origin, self._moved(glyph.origin))
            for glyph, box in zip(glyphs, boxes, strict=True)
        ]

    def letters_in(self, page: int, boxes: list[Rect]) -> list[list[PlacedLetter]]:
        letters = self.driver.letters_in(page, boxes)
        return [
            [replace(each, origin=self._moved(each.origin)) for each in box] for box in letters
        ]

    def _moved(self, origin: Place) -> Place:
        if not self.erased:
            return origin
        shift = self.moves(origin)
        return (origin[0] + shift[0], origin[1] + shift[1])

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def _set_off(box: Rect, was: Place, now: Place) -> Rect:
    across, down = now[0] - was[0], now[1] - was[1]
    return Rect(box.x0 + across, box.y0 + down, box.x1 + across, box.y1 + down)


def _any_box(_box: Rect) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class MovingAfterAnErase:
    """A worker opens the file, so the factory is sent there: no closure."""

    moves: Callable[[Place], Place]
    when: Callable[[Rect], bool] = _any_box

    def __call__(self, path: str) -> PdfDriver:
        erased = Path(path).name != "original.pdf"
        # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
        moving = _MovingDriver(open_driver(path), self.moves, self.when, erased)
        return cast(PdfDriver, moving)


def drawn_page(
    drawing: str, *, title: str = "", link: tuple[str, str] | None = None, pages: int = 1
) -> bytes:
    """By hand, to draw text as some files do: in pieces, spaced, marked, twice, by a quote."""
    doc = pymupdf.open()
    for _number in range(pages):
        page = doc.new_page()
        # A font for the drawing to name: PyMuPDF adds one with the text it writes, off it.
        page.insert_text((0, -100), "x", fontname="helv", fontsize=1)
        [(*_, resource, _referencer)] = page.get_fonts()
        drawn = drawing.replace("/F ", f"/{resource} ").encode()
        doc.update_stream(page.get_contents()[0], drawn)
    page = doc[0]
    if link is not None:
        address, over = link
        [box] = page.search_for(over)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": box, "uri": address})
    doc.set_metadata({"title": title})
    return doc.tobytes()


def upload(client: TestClient, body: bytes) -> Response:
    return client.post(
        "/api/documents", content=body, headers={"content-type": "application/pdf"}
    )


@pytest.fixture
def pdf_bytes(pdf: str) -> bytes:
    return Path(pdf).read_bytes()


@pytest.fixture
def mine(browser: Callable[[], TestClient]) -> TestClient:
    return browser()


@pytest.fixture
def doc(mine: TestClient, pdf_bytes: bytes) -> dict:
    return upload(mine, pdf_bytes).json()


def render(
    client: TestClient,
    doc: dict,
    edits: list[dict],
    regions: list[dict],
    *,
    scale: int = _RENDER_SCALE,
) -> Response:
    body = {"edits": edits, "scale": scale, "regions": regions}
    return client.post(f"/api/documents/{doc['id']}/render", json=body)


def span_starting(doc: dict, page: int, starts: str) -> dict:
    return next(s for s in doc["spans"] if s["page"] == page and s["text"].startswith(starts))


def verdicts_of(rendered: dict) -> dict[str, dict]:
    return {
        span_id: {key: value for key, value in said.items() if key != "runs"}
        for span_id, said in rendered["redactions"].items()
    }


def around(span: dict) -> dict:
    box = span["bbox"]
    return {"page": span["page"], "y0": box["y0"] - _MARGIN_PT, "y1": box["y1"] + _MARGIN_PT}


def log_fields(line: str) -> dict[str, str]:
    return dict(word.split("=", 1) for word in line.split() if "=" in word)


def export_lines(mine, doc: dict, edits: list[dict], capsys) -> list[str]:
    capsys.readouterr()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": edits})
    assert_equal(exported.status_code, 200, "exported")
    return log_lines(capsys.readouterr().out)


def edit_counts(lines: list[str]) -> dict[str, str]:
    [access] = [line for line in lines if " request_done " in line]
    fields = log_fields(access)
    return {name: count for name, count in fields.items() if name.startswith("edits_")}


@dataclass(frozen=True, slots=True)
class _RefusingDriver:
    driver: PdfDriver

    def add_font(self, page: int, font_file: bytes, *, resource: str) -> FontResource:
        """The file's own copies are the fonts the writer names "F…"."""
        if resource.startswith("F"):
            raise DriverError(Message("font_not_added"))
        return self.driver.add_font(page, font_file, resource=resource)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.driver, name)


def refusing_own_copies(path: str) -> PdfDriver:
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, _RefusingDriver(open_driver(path)))
