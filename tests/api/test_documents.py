"""Upload a PDF, get every span back judged, view its pages, delete it."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pymupdf
import pytest

from squidpdf.api import constants as limits
from squidpdf.core import BUILD, face_widths
from squidpdf.core.fonts import FACES
from squidpdf.documents import constants, store
from squidpdf.documents.constants import MAX_IMAGE_PIXELS
from squidpdf.editing.constants import FONT_LIST_CACHE
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_problem, assert_true

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
    assert_equal(response.json()["detail"], "This file is over 100 MB.", "the refusal")
    assert_equal(_kept(), before, "documents on disk after a refusal")


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

    assert_equal(again.status_code, 304, "status of a read the browser has already")
    expires = datetime.fromisoformat(again.headers["squid-expires-at"])
    uploaded = datetime.fromisoformat(doc["expires_at"])
    assert_true(expires >= uploaded, f"it now expires {expires}, uploaded {uploaded}")
    said_in_body = first.json()["expires_at"]
    assert_equal(first.headers["squid-expires-at"], said_in_body, "the header, beside the body")
