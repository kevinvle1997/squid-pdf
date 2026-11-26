"""Each page's letters as MuPDF last read them, kept until a write changes the page.

Every part here that writes forgets what it changes, one page's or every page's, so a read
after a write, even within one of the driver's calls, reads the page as it is now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import squidpdf.core.constants as constants

# One page's letters as get_text("rawdict") reads them: its blocks.
type Blocks = list[dict[str, Any]]


@dataclass(slots=True)
class LettersRead:
    """The last pages' letters read, by page and the flags read with, the latest last.

    At most `LETTERS_READ_KEPT` of them: a page's letters are many objects.
    """

    blocks: dict[tuple[int, int], Blocks] = field(default_factory=dict, repr=False)

    def of(self, page: int, flags: int, read: Callable[[], Blocks]) -> Blocks:
        """Page `page`'s letters read with `flags`: as kept, else read by `read` and kept."""
        key = (page, flags)
        # Taken out and put back last, kept or read now: the latest read stays longest.
        letters = self.blocks.pop(key, None)  # .pop: they may not be kept
        if letters is None:
            letters = read()
        self.blocks[key] = letters
        if len(self.blocks) > constants.LETTERS_READ_KEPT:
            del self.blocks[next(iter(self.blocks))]  # the one read longest ago
        return letters

    def forget_page(self, page: int) -> None:
        """Forget page `page`'s letters: a write just changed it."""
        for key in [
            (read_page, flags) for read_page, flags in self.blocks if read_page == page
        ]:
            del self.blocks[key]

    def forget(self) -> None:
        """Forget every page's letters: a write changed the document, or renumbered it."""
        self.blocks.clear()
