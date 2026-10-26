"""The font names the log may carry: those our own lists hold, never a file's own words."""

from __future__ import annotations

from squidpdf.core.fonts.catalog import CATALOG
from squidpdf.core.fonts.google import google_family_key
from squidpdf.core.fonts.names import bare_name

# Every family we ship, and every document font one of ours has the widths of, by bare name.
_SHIPPED_OR_SAME_WIDTHS = frozenset(
    bare_name(name) for face in CATALOG for name in (face.family, *face.same_widths_as)
)


def loggable_family(font: str) -> str | None:
    """The key one of our lists files `font`'s family under, safe to log; None if none does."""
    key = bare_name(font)
    if key in _SHIPPED_OR_SAME_WIDTHS:
        return key
    return google_family_key(font)
