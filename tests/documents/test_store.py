"""A document is a folder: what's kept comes back the same, and idle ones go."""

from __future__ import annotations

import asyncio
import errno
import os
import threading
import time
from pathlib import Path

import pytest

from squidpdf.core import Fragment, Page, Rect, Span, index_of, new_text
from squidpdf.documents import api as documents, store
from squidpdf.documents.constants import IDLE_S
from squidpdf.documents.errors import Gone, ServerFull
from squidpdf.documents.store import _KeptIndex  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.documents.types import KeptAnalysis
from tests.helpers import (
    assert_at_least,
    assert_equal,
    assert_every_field_filled,
    assert_false,
    assert_in,
    assert_true,
)

_PATIENCE_S = 10  # waited for a second pass; a slow machine needs far less
_RACES = 20  # deletes raced against a writer; before the fix, most left a folder
_WRITER_HEAD_START_S = 0.001  # lets the writer be mid-write when the delete starts


def test_an_id_that_climbs_out_of_the_store_finds_nothing(tmp_path):
    """The router refuses a slash before the store sees it; this is the store's own guard."""
    store.create("owner")  # the store's folder, where a climb would start
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "owner").write_text("anyone")  # shaped like a document
    assert_equal(store.find("../outside"), None, "a lookup that climbs out of the store")


def test_the_sweeper_deletes_only_documents_idle_past_the_hour():
    _, idle = store.create("owner")
    _, fresh = store.create("owner")
    # Not documents: folders of someone else's that share the data folder.
    other = store.root() / "backups"
    other.mkdir()
    others_trash = store.root() / ".trash-backups"
    others_trash.mkdir()
    past = time.time() - IDLE_S - 1
    for folder in (idle, other, others_trash):
        os.utime(folder, (past, past))
    store.sweep()
    assert_false(idle.exists(), "a document idle past the hour is still on disk")
    assert_true(fresh.exists(), "a document in use was swept")
    assert_true(other.exists(), "a folder that isn't a document was swept")
    assert_true(others_trash.exists(), "a folder named like trash, not a document's, was swept")


def test_a_failed_sweep_is_logged_and_sweeping_carries_on(monkeypatch, caplog):
    """Stopping would keep every document on disk from then on."""
    passes = 0

    def fails_the_first_time() -> None:
        nonlocal passes
        passes += 1
        if passes == 1:
            raise OSError("the disk said no")

    monkeypatch.setattr(documents, "SWEEP_EVERY_S", 0)
    monkeypatch.setattr(store, "sweep", fails_the_first_time)

    async def sweep_until_a_second_pass() -> None:
        """Sweep until a pass runs after the failed one, or patience runs out."""
        sweeping = asyncio.create_task(documents.sweep_forever())
        deadline = time.monotonic() + _PATIENCE_S
        # Waits on the passes themselves, not a fixed moment a slow runner can miss.
        while passes < 2 and not sweeping.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        sweeping.cancel()

    asyncio.run(sweep_until_a_second_pass())
    assert_at_least(passes, 2, "sweeps, the one that failed and those after it")
    assert_in("the disk said no", caplog.text, "what the log says about the failed sweep")


@pytest.mark.parametrize(
    ("failure", "raised"),
    [
        # Full, not broken: the reader is told the server is full, not of a bug.
        (OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), ServerFull),
        (OSError(errno.EIO, os.strerror(errno.EIO)), OSError),
    ],
    ids=["the disk full", "the disk broken"],
)
def test_a_save_cut_short_keeps_what_was_there_whole(monkeypatch, failure, raised):
    """A reader must see the old file or the new one, never half: half reads as broken JSON."""
    _, folder = store.create("owner")
    first = KeptAnalysis(b'{"worked": "out"}', b"[]", "its-digest")
    store.save_analysis(folder, "a-build", first)
    whole = sorted(path.name for path in folder.iterdir())

    def cut_short(*_paths: object) -> None:
        raise failure

    monkeypatch.setattr(store.os, "replace", cut_short)
    with pytest.raises(raised):
        again = KeptAnalysis(b'{"worked": "out again"}', b"[1]", "its-next-digest")
        store.save_analysis(folder, "a-build", again)

    assert_equal(store.load_analysis(folder, "a-build"), first, "the analysis kept")
    names = sorted(path.name for path in folder.iterdir())
    assert_equal(names, whole, "files in the folder, as the whole save left them")


def test_touching_a_document_deleted_meanwhile_says_it_is_gone():
    """Found, then deleted by its owner or the sweep before its hour restarts."""
    _, folder = store.create("owner")
    store.delete(folder)
    with pytest.raises(Gone):
        store.touch(folder)


def test_a_document_deleted_while_a_worker_writes_leaves_nothing_behind():
    """A cancelled upload's worker can still be writing when the folder is deleted.

    A file landing mid-delete used to keep the folder on disk, its owner file gone.
    """
    for _ in range(_RACES):
        _, folder = store.create("owner")
        writing = threading.Event()
        stop = threading.Event()
        writer = threading.Thread(target=_write_until_gone, args=(folder, writing, stop))
        writer.start()
        writing.wait()
        time.sleep(_WRITER_HEAD_START_S)
        store.delete(folder)
        stop.set()
        writer.join()
    assert_equal(sorted(store.root().iterdir()), [], "folders left in the store")


def _write_until_gone(folder: Path, writing: threading.Event, stop: threading.Event) -> None:
    """Save the index over and over, as analysis does, until the folder goes."""
    writing.set()
    while not stop.is_set():
        try:
            store.save_index(folder, index_of([]))
        except Gone:  # deleted under it: the worker's request ends there
            return


def test_the_sweeper_clears_what_a_delete_cut_short_left():
    """A delete moves the folder aside, then empties it; a crash between leaves it there.

    So does a delete from before they were moved aside: a folder named like a
    document with no owner file, which the sweep takes once it is idle too.
    """
    _, trashed = store.create("owner")
    cut_short = store.root() / f".trash-{trashed.name}"
    trashed.rename(cut_short)
    _, emptied = store.create("owner")
    (emptied / "owner").unlink()
    _, being_made = store.create("owner")
    (being_made / "owner").unlink()  # as create leaves it, before it writes the owner
    past = time.time() - IDLE_S - 1
    os.utime(emptied, (past, past))
    store.sweep()
    assert_false(cut_short.exists(), "a delete cut short is still on disk")
    assert_false(emptied.exists(), "a folder a delete emptied is still on disk")
    assert_true(being_made.exists(), "a document being made was swept")


def test_writing_into_a_document_deleted_meanwhile_says_it_is_gone(engine):
    """A worker found it, then its owner deleted it: analysis ends Gone, not a 500."""
    _, folder = store.create("owner")
    store.delete(folder)
    with pytest.raises(Gone):
        store.save_index(folder, engine.index())
    with pytest.raises(Gone):
        store.save_analysis(folder, "a-build", KeptAnalysis(b"{}", b"[]", "its-digest"))


def test_a_worker_reads_an_index_once(engine):
    """Every render and export needs it, and parsing it was most of a render's time."""
    _, folder = store.create("owner")
    store.save_index(folder, engine.index())
    first = store.require_index(folder)
    assert_true(store.require_index(folder) is first, "the index was read from disk again")


def test_a_kept_index_never_outlives_its_file(engine):
    """Saved again, the new one is read; deleted, there is none."""
    _, folder = store.create("owner")
    store.save_index(folder, engine.index())
    store.require_index(folder)
    first_span = index_of(list(engine.index())[:1])
    store.save_index(folder, first_span)  # at once: the file's clock may not have moved
    read = [span.id for span in store.require_index(folder)]
    assert_equal(read, [span.id for span in first_span], "spans in the index read again")
    store.delete(folder)
    with pytest.raises(Gone):
        store.require_index(folder)


def test_every_field_of_a_kept_span_and_page_comes_back_as_it_was():
    """A span's id and facts never change, so what's kept must read back whole.

    Every field is off a blank one's, so one added later and left at its default fails here.
    Two fragments, since a merged span is redrawn from them.
    """
    first_fragment = Fragment("Total ", Rect(10.0, 20.0, 40.0, 32.0), (10.0, 30.0))
    second_fragment = Fragment("due", Rect(40.5, 20.0, 60.0, 32.0), (40.5, 30.0))
    span = Span(
        id="a1b2c3d4e5f6",
        page=1,
        text="Total due",
        font="ABCDEE+Calibri",
        size=11.5,
        color=(0.1, 0.2, 0.3),
        opacity=0.5,
        bbox=first_fragment.bbox.union(second_fragment.bbox),
        origin=(10.0, 30.0),
        fragments=(first_fragment, second_fragment),
        direction=(0.0, -1.0),
    )
    blank_span = new_text(0, origin=(0.0, 0.0), text="", size=1.0, font="")
    assert_every_field_filled(span, blank_span, "the span kept")
    blank_fragment = Fragment("", Rect(0.0, 0.0, 0.0, 0.0), (0.0, 0.0))
    for fragment in span.fragments:
        assert_every_field_filled(fragment, blank_fragment, "each fragment kept")
    page = Page(width=612.0, height=792.0, turn_cw=90)
    blank_page = Page(0.0, 0.0, 0)
    assert_every_field_filled(page, blank_page, "the page kept")
    _, folder = store.create("owner")

    store.save_index(folder, index_of([span]))
    store.save_pages(folder, [page])

    assert_equal(list(store.require_index(folder)), [span], "the spans read back")
    assert_equal(store.load_pages(folder), [page], "the pages read back")


def test_a_kept_index_that_forgets_equals_a_fresh_one(engine, tmp_path):
    """What a worker keeps between requests: forgotten, nothing of the last index stays."""
    kept = _KeptIndex()
    kept.index_for((tmp_path, 1, 2), engine.index)
    assert_every_field_filled(kept, _KeptIndex(), "the record once it keeps an index")

    kept.forget()

    assert_equal(kept, _KeptIndex(), "the record once it forgot")
