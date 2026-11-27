"""No font moves a room or a line, so they're worked out once and kept."""

from __future__ import annotations

from pathlib import Path

from squidpdf.core import BUILD, FreeSpace, SpanIndex
from squidpdf.documents import store
from squidpdf.documents.store import DocumentStore


def kept_free_space(documents: DocumentStore, folder: Path, index: SpanIndex) -> FreeSpace:
    kept = store.load_free_space(folder, BUILD)
    if kept is not None:
        return kept
    with documents.open_original(folder) as engine:
        free_space = engine.free_space(index)
    store.save_free_space(folder, BUILD, free_space)
    return free_space
