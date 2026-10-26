"""The font names the log may carry: those our own lists hold, never a file's own words."""

from __future__ import annotations

from squidpdf.core.fonts.catalog import CATALOG
from squidpdf.core.fonts.google import google_key
from squidpdf.core.fonts.names import bare_name

# Every family we ship, and every document font one of ours has the widths of, by bare name.
_SHIPPED_OR_MATCHED = frozenset(
    bare_name(name) for face in CATALOG for name in (face.family, *face.same_widths_as)
)


def listed_family(font: str) -> str | None:
    """The key our own lists file a font's family under; None when none of them lists it.

    Ours: a family we ship, a font one of ours has the widths of, or one of Google's.
    """
    key = bare_name(font)
    if key in _SHIPPED_OR_MATCHED:
        return key
    return google_key(font)
