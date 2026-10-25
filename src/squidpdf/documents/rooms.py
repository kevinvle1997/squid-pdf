"""Every span's room, worked out once from the original and kept beside its analysis.

No font moves a room, so a render, an export and attaching a copy read them, not the pages.
"""

from __future__ import annotations

from pathlib import Path

from squidpdf.core import BUILD, SpanIndex
from squidpdf.documents import store


def kept_rooms(folder: Path, index: SpanIndex) -> dict[str, float]:
    """Every span's room, by id, as kept; worked out from the original, and kept, when it isn't.

    Read before the original is opened, as the index is: a delete after that can't take it.
    """
    kept = store.load_rooms(folder, BUILD)
    # Kept under this build and tuning: no page is read.
    if kept is not None:
        return kept
    # After a deploy or a retune: every page read again, once.
    with store.open_original(folder) as engine:
        rooms = engine.rooms(index, index)
    store.save_rooms(folder, BUILD, rooms)
    return rooms
