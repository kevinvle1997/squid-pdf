"""The user's copies of a document's fonts: each kept, replaced and removed whole."""

from __future__ import annotations

import pytest

from squidpdf.core import index_of
from squidpdf.documents import attached, store
from squidpdf.documents.store import FOLDER_STORE
from tests.helpers import assert_equal


def test_a_copy_of_a_font_removed_after_it_was_listed_reads_as_no_copy_not_no_document():
    """Listed by a render as an attach replaced it: it lends nothing, and the document stays.

    Gone would answer 404, and the browser would open the whole document again.
    """
    doc_id, folder = FOLDER_STORE.create("owner")
    store.save_index(folder, index_of([]))  # found only when its index is kept
    arrived = attached.arriving_font(folder)
    arrived.write_bytes(b"a font")
    attached.keep_font(folder, "Arial", arrived)
    listed = attached.attached_files(folder)
    attached.drop_font(folder, "Arial")

    with pytest.raises(KeyError):
        listed["Arial"]
    assert_equal(FOLDER_STORE.find(doc_id), (folder, "owner"), "the document, found")


def test_a_copy_replaced_again_and_again_then_removed_leaves_none(tmp_path):
    """Each replacement drops the copy before it, whichever the folder lists first."""
    folder = tmp_path / "doc"
    folder.mkdir()
    for round_number in range(20):
        arrived = attached.arriving_font(folder)
        arrived.write_bytes(b"copy %d" % round_number)
        attached.keep_font(folder, "Arial", arrived)

    kept_before = len(attached.attached_files(folder).paths)
    attached.drop_font(folder, "Arial")

    assert_equal(kept_before, 1, "copies kept for one font")
    assert_equal(list((folder / "fonts").glob("[!.]*")), [], "copies left once removed")
