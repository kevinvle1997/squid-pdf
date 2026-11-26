"""The free space after each span (its room), and each line: worked out once, and kept.

No font moves a room or a line, so a render, an export and attaching a copy read them,
not the pages.
"""

from __future__ import annotations

from pathlib import Path

from squidpdf.core import BUILD, FreeSpace, SpanIndex
from squidpdf.documents import store
from squidpdf.documents.store import DocumentStore


def kept_free_space(documents: DocumentStore, folder: Path, index: SpanIndex) -> FreeSpace:
    """Every span's room and every line, as kept; worked out from the original when they aren't.

    Read before the original is opened, as the index is: a delete after that can't take it.
    """
    kept = store.load_free_space(folder, BUILD)
    # Kept under this build and tuning: no page is read.
    if kept is not None:
        return kept
    # After a deploy or a retune: every page read again, once.
    with documents.open_original(folder) as engine:
        free_space = engine.free_space(index)
    store.save_free_space(folder, BUILD, free_space)
    return free_space
