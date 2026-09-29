"""Documents' controllers do the whole action, reply and all, with no web server."""

from __future__ import annotations

import asyncio
import math
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import orjson
import pymupdf
import pytest

from squidpdf.core import BUILD
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.constants import MAX_PAGES, PAGE_CACHE
from squidpdf.documents.pages import PageController
from squidpdf.documents.read import ReadController
from squidpdf.documents.types import Loaded
from squidpdf.documents.upload import UploadController
from tests.helpers import assert_equal, assert_not_in, assert_true

_CHUNK = 4096  # a network-sized piece, so the upload arrives in several


class _InProcess:
    """Runs pool work in the test's own process: the controllers take any workers."""

    async def run(self, timeout, task):
        return task()


@pytest.fixture
def doc(pdf) -> Loaded:
    """A stored, analysed document holding the sample as its original."""
    doc_id, folder = store.create("owner")
    shutil.copy(pdf, folder / store.ORIGINAL)
    analyse(str(folder), MAX_PAGES)
    return Loaded(doc_id, folder, store.touch(folder))


def test_a_page_is_drawn_with_no_web_server_in_between(doc):
    page = store.load_pages(doc.folder)[1]

    reply = asyncio.run(PageController(_InProcess()).page(doc, page=1, scale=2, build=BUILD))

    image = pymupdf.Pixmap(reply.png)
    size = (image.width, image.height)
    # MuPDF rounds each side up.
    expected = (math.ceil(page.width * 2), math.ceil(page.height * 2))
    assert_equal(size, expected, "the image's size")
    assert_equal(reply.headers, {"Cache-Control": PAGE_CACHE}, "the reply's headers")


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    """The body as it arrives over the network: a piece at a time."""
    for start in range(0, len(body), _CHUNK):
        yield body[start : start + _CHUNK]


def test_an_upload_is_kept_and_judged_with_no_web_server_in_between(pdf):
    body = Path(pdf).read_bytes()
    upload = UploadController(_InProcess()).upload(
        "owner", declared=None, chunks=_chunks(body), said_in="en"
    )

    reply = asyncio.run(upload)

    found = store.find(reply.body["id"])
    if found is None:
        pytest.fail("the upload wasn't kept")
    folder, owner = found
    assert_equal(owner, "owner", "whose the upload is")
    assert_equal((folder / store.ORIGINAL).read_bytes(), body, "the original as kept")
    assert_equal(len(reply.body["pages"]), 2, "pages judged")
    assert_true(len(reply.body["spans"]) > 0, "no spans judged")
    assert_equal(reply.headers["Content-Language"], "en", "the reply's language")


def test_a_document_reads_back_and_then_as_unchanged_with_no_web_server_in_between(doc):
    read = ReadController(_InProcess())

    first = asyncio.run(read.read(doc, said_in="en", if_none_match=None))
    again = asyncio.run(read.read(doc, said_in="en", if_none_match=first.headers["ETag"]))

    kept = store.load_analysis(doc.folder, BUILD)
    if kept is None:
        pytest.fail("the analysis wasn't kept")
    spans = orjson.loads(first.body)["spans"]
    assert_equal(spans, orjson.loads(kept)["spans"], "the spans read back")
    assert_equal((again.status, again.body), (304, b""), "the reply once the browser has it")
    assert_equal(
        again.headers["ETag"], first.headers["ETag"], "the ETag once the browser has it"
    )
    assert_not_in("Content-Type", again.headers, "the 304's headers")
