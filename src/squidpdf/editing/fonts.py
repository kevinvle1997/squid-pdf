"""Pool work for the font list: every face new text can be drawn in, with its widths.

Framework-free, so the pool can run it: reading every face's letters takes a
few seconds, too long for the server's own thread.
"""

from __future__ import annotations

from squidpdf.core import BUILD, face_glyphs
from squidpdf.core.fonts import CATALOG
from squidpdf.editing.types import FamilyInfo, FontList


def font_list() -> FontList:
    """Every face we ship, grouped by family, in the catalog's order."""
    families: dict[str, FamilyInfo] = {}
    for face in CATALOG:
        new_family: FamilyInfo = {
            "family": face.family,
            "category": face.category,
            "license": face.license,
            "same_widths_as": list(face.same_widths_as),
            "faces": [],
        }
        family = families.setdefault(face.family, new_family)
        family["faces"].append(
            {"name": face.name, "style": face.style, "glyphs": face_glyphs(face)}
        )
    return {"build": BUILD, "families": list(families.values())}
