"""A page the document lacks, wherever a request names one: one check, two outcomes.

An edit that names one is skipped and said, as any edit pointing at nothing is.
The request's own pages, a region to draw or the pages to export, are refused.
"""

from __future__ import annotations

import pytest

from tests.helpers import assert_equal, assert_problem

_SCALE = 2
_PAST_THE_END = 2  # the sample has pages 0 and 1


def _insert(page: int) -> dict:
    """New text on `page`."""
    return {"kind": "insert", "page": page, "origin": [72, 700], "text": "Signed", "size": 12}


@pytest.mark.parametrize("page", [_PAST_THE_END, -1], ids=["past the end", "before the start"])
@pytest.mark.parametrize("names_it", ["an insert", "a region", "the pages to export"])
def test_a_page_the_document_lacks_is_skipped_in_an_edit_and_refused_in_a_request(
    mine, doc, names_it, page
):
    """The same question everywhere: does the document have this page? Then two answers."""
    render = f"/api/documents/{doc['id']}/render"
    export = f"/api/documents/{doc['id']}/export"

    if names_it == "an insert":
        body = {"edits": [_insert(page)], "scale": _SCALE, "regions": []}
        skipped = mine.post(render, json=body).json()["skipped"]
        got = [(s["edit"], s["type"], s["code"]) for s in skipped]
        assert_equal(got, [(0, "bad_reference", "no_page")], "the edits left out")
        return
    if names_it == "a region":
        body = {"edits": [], "scale": _SCALE, "regions": [{"page": page}]}
        assert_problem(mine.post(render, json=body), "no_such_page", 422)
        return
    assert_problem(mine.post(export, json={"edits": [], "pages": [page]}), "no_such_page", 422)
