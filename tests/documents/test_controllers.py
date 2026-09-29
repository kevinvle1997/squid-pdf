"""Documents' controllers do the whole action, reply and all, with no web server."""

from __future__ import annotations

import asyncio
import math
import shutil

import pymupdf
import pytest

from squidpdf.core import BUILD
from squidpdf.documents import store
from squidpdf.documents.analyse import analyse
from squidpdf.documents.constants import MAX_PAGES, PAGE_CACHE
from squidpdf.documents.pages import PageController
from squidpdf.documents.types import Loaded
from tests.helpers import assert_equal


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
