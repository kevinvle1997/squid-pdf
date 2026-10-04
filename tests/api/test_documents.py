"""Upload a PDF, get every span back judged, view its pages, delete it."""

from __future__ import annotations

import asyncio
import errno
import io
import os
import shutil
from collections.abc import AsyncIterator
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import IO, Any

import httpx
import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squidpdf.api import constants as limits
from squidpdf.core import BUILD, face_widths, words
from squidpdf.core.fonts.catalog import FACES
from squidpdf.documents import constants, replies, store
from squidpdf.documents.constants import MAX_IMAGE_PIXELS
from squidpdf.documents.types import KeptAnalysis
from squidpdf.documents.upload import _UploadsUnderWay  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.editing.constants import FONT_LIST_CACHE
from tests.api.conftest import BASE_URL, upload
from tests.helpers import (
    assert_at_least,
    assert_equal,
    assert_every_field_filled,
    assert_not_in,
    assert_problem,
    assert_true,
)

_HUGE_PT = 3000  # a page side past the pixel limit at every scale above 1


def _kept() -> int:
    """How many documents are on disk."""
    return len(os.listdir(store.root()))


def test_the_font_list_names_every_face_we_ship_and_keeps_for_good(mine):
    response = mine.get("/api/fonts", params={"build": BUILD})
    assert_equal(response.headers["cache-control"], FONT_LIST_CACHE, "font list caching")
    listed = response.json()
    assert_equal(listed["build"], BUILD, "build")
    families = {family["family"]: family for family in listed["families"]}
    assert_equal(families["Carlito"]["same_widths_as"], ["Calibri"], "what Carlito matches")
    assert_equal(families["Caveat"]["category"], "handwriting", "Caveat's kind")
    faces = {face["name"]: face for family in families.values() for face in family["faces"]}
    assert_equal(set(faces), set(FACES), "every face an insert can ask for")
    # The widths the server measures with, so a preview matches the draw.
    carlito_bold = faces["Carlito Bold"]
    assert_equal(carlito_bold["style"], "bold", "its style")
    assert_equal(carlito_bold["glyphs"], face_widths(FACES["Carlito Bold"]), "its widths")

    # An old build still gets it, but not to keep.
    old = mine.get("/api/fonts", params={"build": "an-older-build"})
    assert_equal(old.headers["cache-control"], "no-store", "caching under an old build")


def test_a_page_is_a_png_the_browser_keeps_for_an_hour(mine, doc):
    response = mine.get(
        f"/api/documents/{doc['id']}/pages/0", params={"scale": 2, "build": doc["build"]}
    )
    assert_equal(response.headers["content-type"], "image/png", "page media type")
    assert_equal(
        response.headers["cache-control"], "private, max-age=3600, immutable", "page caching"
    )
    pix = pymupdf.Pixmap(response.content)
    assert_equal((pix.width, pix.height), (595 * 2, 842 * 2), "pixels at scale 2")

    # Under an old build it's still drawn, but not to keep.
    params = {"scale": 1, "build": "an-older-build"}
    old = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params)
    assert_equal(old.headers["cache-control"], "no-store", "caching under an old build")

    # A page it doesn't have is its own problem: "not found" would make the browser re-upload.
    params = {"scale": 2, "build": doc["build"]}
    assert_problem(
        mine.get(f"/api/documents/{doc['id']}/pages/9", params=params), "no_such_page", 422
    )


def test_a_page_past_the_pixel_limit_gets_a_smaller_scale(mine):
    big = pymupdf.open()
    big.new_page(width=_HUGE_PT, height=_HUGE_PT)
    doc = upload(mine, big.tobytes()).json()
    params = {"scale": max(limits.PAGE_SCALES), "build": doc["build"]}
    png = mine.get(f"/api/documents/{doc['id']}/pages/0", params=params).content
    pix = pymupdf.Pixmap(png)
    assert_true(
        pix.width * pix.height <= MAX_IMAGE_PIXELS,
        f"{pix.width}x{pix.height} is past the pixel limit",
    )


def test_deleting_it_leaves_nothing_behind(mine, doc):
    before = _kept()
    assert_equal(mine.delete(f"/api/documents/{doc['id']}").status_code, 204, "delete status")
    assert_problem(mine.get(f"/api/documents/{doc['id']}"), "not_found", 404)
    assert_equal(_kept(), before - 1, "documents on disk after a delete")


_AES_256 = 5  # pymupdf.PDF_ENCRYPT_AES_256, which its type stubs leave out


def _locked() -> bytes:
    """A real one-page PDF that opens only with a password."""
    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes(encryption=_AES_256, user_pw="user", owner_pw="owner")


def _written_out(objects: list[str]) -> bytes:
    """A PDF written by hand from its objects, numbered from 1, with a true index of them.

    Everything else about it is sound, so what the test gives it is its only fault.
    """
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    index_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{index_at}\n%%EOF\n".encode()
    return bytes(out)


_CATALOG = "<< /Type /Catalog /Pages 2 0 R >>"
_PAGE = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"


@pytest.mark.parametrize(
    ("body", "problem", "status"),
    [
        (b"Dear Sir, please find attached.", "not_a_pdf", 415),
        # Starts like a PDF, then every byte value over and over: no PDF inside.
        (b"%PDF-1.7\n" + bytes(range(256)) * 8, "damaged", 422),
        (_locked(), "encrypted", 422),
        # The page list holds itself: reading it never ends.
        (
            _written_out([_CATALOG, "<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>", _PAGE]),
            "damaged",
            422,
        ),
        # It says five pages and has one.
        (
            _written_out([_CATALOG, "<< /Type /Pages /Kids [3 0 R] /Count 5 >>", _PAGE]),
            "damaged",
            422,
        ),
        (_written_out([_CATALOG, "<< /Type /Pages /Kids [] /Count 0 >>"]), "damaged", 422),
    ],
    ids=[
        "text",
        "garbage after the header",
        "password-protected",
        "a page list inside itself",
        "a page count that lies",
        "no pages at all",
    ],
)
def test_a_file_that_wont_open_is_refused_and_nothing_kept(mine, body, problem, status):
    before = _kept()
    assert_problem(upload(mine, body), problem, status)
    assert_equal(_kept(), before, "documents on disk after a refusal")


def test_a_file_over_the_limit_is_refused_while_it_streams(mine, pdf_bytes, monkeypatch):
    monkeypatch.setattr(constants, "MAX_FILE_BYTES", len(pdf_bytes) // 2)
    before = _kept()
    chunks = iter([pdf_bytes[:1024], pdf_bytes[1024:]])  # no length up front: it streams
    response = mine.post("/api/documents", content=chunks)
    assert_problem(response, "too_large", 413)
    said = words.sentence("too_large").format(mb=constants.MAX_FILE_MB)
    assert_equal(response.json()["detail"], said, "the refusal")
    assert_equal(_kept(), before, "documents on disk after a refusal")


def test_an_upload_is_refused_while_the_disk_is_nearly_full(mine, pdf_bytes, monkeypatch):
    """The server says so and keeps nothing, rather than fill the disk every document is on."""
    whole_disk = shutil.disk_usage(store.root()).total
    monkeypatch.setattr(constants, "MIN_FREE_BYTES", whole_disk + 1)
    before = _kept()
    assert_problem(upload(mine, pdf_bytes), "server_full", 503)
    assert_equal(_kept(), before, "documents on disk after a refusal")


async def _uploads_at_once(app: FastAPI, pdf_bytes: bytes, *, count: int) -> list[int]:
    """The statuses of `count` uploads held partway at once, then of one upload after them.

    On the server's own loop, since a TestClient sends each file whole before the next.
    """
    go_on = asyncio.Event()

    async def slow_file(asked_for_more: asyncio.Event) -> AsyncIterator[bytes]:
        """The file, held back after its first chunk until the test lets it go on."""
        yield pdf_bytes[:1024]
        asked_for_more.set()
        await go_on.wait()
        yield pdf_bytes[1024:]

    async def held_partway(asked_for_more: asyncio.Event, sent: asyncio.Task) -> None:
        """Until this upload passed its check and is writing, or was answered first."""
        writing = asyncio.ensure_future(asked_for_more.wait())
        await asyncio.wait({writing, sent}, return_when=asyncio.FIRST_COMPLETED)
        writing.cancel()

    pdf = {"content-type": "application/pdf"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as browser:
        asked = [asyncio.Event() for _ in range(count)]
        sending = [
            asyncio.create_task(
                browser.post("/api/documents", content=slow_file(asked_for_more), headers=pdf)
            )
            for asked_for_more in asked
        ]
        await asyncio.gather(*map(held_partway, asked, sending, strict=True))
        go_on.set()
        at_once = sorted(sent.status_code for sent in await asyncio.gather(*sending))
        after = await browser.post("/api/documents", content=pdf_bytes, headers=pdf)
    return [*at_once, after.status_code]


def test_uploads_under_way_count_against_the_free_disk_floor(
    app, server: TestClient, pdf_bytes, monkeypatch
):
    """Uploads at once pass only while each has room for a whole file."""
    disk = shutil.disk_usage(store.root())
    # Ten whole-disk files fit, not eleven; half a disk either way for other tests' writes.
    monkeypatch.setattr(constants, "MAX_FILE_BYTES", disk.total)
    monkeypatch.setattr(
        constants, "MIN_FREE_BYTES", disk.free - 10 * disk.total - disk.total // 2
    )

    if server.portal is None:
        pytest.fail("the app isn't started")
    statuses = server.portal.call(partial(_uploads_at_once, app, pdf_bytes, count=20))

    # Ten fit and ten are refused; once they're in, there's room again.
    assert_equal(statuses, [*[201] * 10, *[503] * 10, 201], "twenty at once, then one after")


def test_an_upload_refused_as_it_streams_gives_back_the_disk_it_held(
    mine, pdf_bytes, monkeypatch
):
    """Refused after its check, an upload holds nothing: refusals never fill the floor."""
    disk = shutil.disk_usage(store.root())
    # Room for one whole-disk file, not two; half a disk either way, as above.
    monkeypatch.setattr(constants, "MAX_FILE_BYTES", disk.total)
    monkeypatch.setattr(constants, "MIN_FREE_BYTES", disk.free - disk.total - disk.total // 2)
    not_a_pdf = b"Dear Sir, please find attached."

    # Each passes the check and is refused only once its file is in.
    assert_problem(upload(mine, not_a_pdf), "not_a_pdf", 415)
    assert_problem(upload(mine, not_a_pdf), "not_a_pdf", 415)
    assert_equal(upload(mine, pdf_bytes).status_code, 201, "an upload after two refused")


def test_uploads_under_way_that_forget_equal_fresh_ones():
    """Forgetting the uploads under way leaves a fresh record."""
    under_way = _UploadsUnderWay()
    with under_way.holding(1024, free=constants.MIN_FREE_BYTES + 1024):
        assert_every_field_filled(under_way, _UploadsUnderWay(), "the record while one streams")
        under_way.forget()
        assert_equal(under_way, _UploadsUnderWay(), "the record once it forgot")


def _write_on_a_full_disk(*_args: object, **_kwargs: object) -> int:
    """A write the disk has no room left for."""
    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


class _FileOnAFullDisk(io.FileIO):
    """A file opened on a disk with no room left: every write is refused."""

    def write(self, _data: object, /) -> int:
        """Refused: the disk is full."""
        return _write_on_a_full_disk()


def _original_on_a_full_disk(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
    """`path` opened as Path.open opens it, but the upload's original on a full disk."""
    if path.name == store.ORIGINAL:
        return _FileOnAFullDisk(path, "wb")
    return open(path, mode, *args, **kwargs)


# An upload's first write is its owner's, and its largest its original's.
@pytest.mark.parametrize(
    ("method", "on_a_full_disk"),
    [("write_text", _write_on_a_full_disk), ("open", _original_on_a_full_disk)],
    ids=["writing its owner", "writing its original"],
)
def test_a_disk_that_fills_up_as_an_upload_is_kept_is_server_full(
    mine, pdf_bytes, monkeypatch, method, on_a_full_disk
):
    """A disk that runs out past the floor's check is server_full, not a bug."""
    monkeypatch.setattr(Path, method, on_a_full_disk)
    before = _kept()
    assert_problem(upload(mine, pdf_bytes), "server_full", 503)
    assert_equal(_kept(), before, "documents on disk after a refusal")


def test_the_first_upload_makes_the_folder_documents_are_kept_in(
    mine, pdf_bytes, monkeypatch, tmp_path
):
    """As on a server's first start, before anything was kept."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "not made yet"))
    assert_equal(upload(mine, pdf_bytes).status_code, 201, "status of the first upload")


@pytest.mark.parametrize(
    "path", ["/api/documents", "/api/documents/any/render"], ids=["an upload", "an edit list"]
)
def test_a_body_whose_size_isnt_a_whole_number_is_a_bad_request(mine, pdf_bytes, path):
    """A past bug: int() made "many" a 500. Uploads and edit lists each read it."""
    before = _kept()
    headers = {"content-type": "application/pdf", "content-length": "many"}
    response = mine.post(path, content=pdf_bytes, headers=headers)
    assert_problem(response, "invalid_request", 400)
    assert_equal(_kept(), before, "documents on disk after a refusal")


@pytest.mark.parametrize(
    ("path", "deleted"),
    [
        ("", "the whole folder"),
        (
            "/pages/0?scale=1&build=any",
            "only the original, as a sweep partway through leaves it",
        ),
    ],
    ids=["read", "a page"],
)
def test_a_document_deleted_while_its_request_runs_is_not_found(
    mine, doc, monkeypatch, path, deleted
):
    """The browser uploads again on not_found; a server error would leave it stuck."""
    touch = store.touch

    def touch_then_lose(folder: Path) -> float:
        expires = touch(folder)
        if deleted == "the whole folder":
            shutil.rmtree(folder)
        else:
            (folder / store.ORIGINAL).unlink()
        return expires

    monkeypatch.setattr(store, "touch", touch_then_lose)
    assert_problem(mine.get(f"/api/documents/{doc['id']}{path}"), "not_found", 404)


def test_a_read_says_when_the_document_now_expires_even_with_no_body(mine, doc):
    """Reading restarts its hour; a 304 has no body to carry expires_at in, so a header does."""
    url = f"/api/documents/{doc['id']}"
    first = mine.get(url)
    again = mine.get(url, headers={"if-none-match": first.headers["etag"]})

    assert_equal(first.headers["content-type"], "application/json", "the body's type")
    assert_equal(again.status_code, 304, "status of a read the browser has already")
    assert_not_in("content-type", again.headers, "the type of a body a 304 doesn't have")
    expires = datetime.fromisoformat(again.headers["squid-expires-at"])
    uploaded = datetime.fromisoformat(doc["expires_at"])
    assert_true(expires >= uploaded, f"it now expires {expires}, uploaded {uploaded}")
    said_in_body = first.json()["expires_at"]
    assert_equal(first.headers["squid-expires-at"], said_in_body, "the header, beside the body")


_REWORDED = "{chars} isn't in this font, so the line is drawn in {font}"


@pytest.mark.parametrize(
    ("change", "now", "sent_as"),
    [
        (
            lambda patch, now: patch.setattr(replies, "TOLERANCE_PT", now),
            replies.TOLERANCE_PT / 2,
            ("fit", "tolerance_pt"),
        ),
        (
            lambda patch, now: patch.setattr(replies, "CONDENSE_LIMIT", now),
            replies.CONDENSE_LIMIT / 2,
            ("fit", "condense_limit"),
        ),
        (
            lambda patch, now: patch.setattr(replies, "SHRINK_FLOOR", now),
            replies.SHRINK_FLOOR / 2,
            ("fit", "shrink_floor"),
        ),
        (
            lambda patch, now: patch.setitem(words.CATALOGS[words.ENGLISH], "missing", now),
            _REWORDED,
            ("copy", "missing"),
        ),
        (
            lambda patch, now: patch.setattr(constants, "REPLY_VERSION", now),
            constants.REPLY_VERSION + 1,
            None,  # not in the reply: the 200 alone shows it
        ),
    ],
    ids=[
        "a fit tolerance",
        "a condense limit",
        "a shrink floor",
        "a sentence",
        "the reply's shape",
    ],
)
def test_a_read_after_what_its_reply_is_made_of_changes_gets_a_new_body_not_a_304(
    mine, doc, monkeypatch, change, now, sent_as
):
    """A deploy that changes a reply, with no new build, reaches every open document."""
    url = f"/api/documents/{doc['id']}"
    first = mine.get(url)
    change(monkeypatch, now)
    again = mine.get(url, headers={"if-none-match": first.headers["etag"]})

    assert_equal(again.status_code, 200, "status of a read after the change")
    if sent_as is not None:
        part, name = sent_as
        assert_equal(again.json()[part][name], now, "what the reply now sends")


def test_a_read_the_browser_has_already_reads_no_analysis(mine, doc, monkeypatch):
    """A 304 carries no body, so its spans, nearly all of a long document, aren't read."""
    url = f"/api/documents/{doc['id']}"
    first = mine.get(url)
    reads: list[object] = []
    load_analysis = store.load_analysis

    def counted(folder, *args, **kwargs) -> KeptAnalysis | None:
        reads.append(folder)
        return load_analysis(folder, *args, **kwargs)

    monkeypatch.setattr(store, "load_analysis", counted)
    again = mine.get(url, headers={"if-none-match": first.headers["etag"]})

    assert_equal(again.status_code, 304, "status of a read the browser has already")
    assert_equal(len(reads), 0, "times the analysis was read")


def test_an_upload_answers_exactly_what_a_read_does(mine, doc):
    """Both send the spans as the analysis kept them, spliced in unread."""
    read = mine.get(f"/api/documents/{doc['id']}").json()
    assert_equal(read | {"expires_at": doc["expires_at"]}, doc, "the upload beside a read")
    assert_at_least(len(doc["spans"]), 1, "spans in the sample")
