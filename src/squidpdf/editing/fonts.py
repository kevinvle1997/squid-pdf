"""The font list: every face new text can be drawn in, with its widths. No web framework."""

from __future__ import annotations

import orjson

# Not through `Engine`: these are our own files, with no document to open.
from squidpdf.core import BUILD, Workers, face_widths
from squidpdf.core.fonts import CATALOG
from squidpdf.editing.constants import FONT_LIST_CACHE, FONT_LIST_TIMEOUT_S
from squidpdf.editing.types import FamilyInfo, FontList, FontListReply

__all__ = [
    "FontListController",
]

# The same for everyone under a build, and 5 s of pool work (measured): made once per server.
_font_lists: dict[str, bytes] = {}


class FontListController:
    """The font list, from request to reply."""

    def __init__(self, workers: Workers) -> None:
        """List the fonts on `workers`, off the server's own thread."""
        self._workers = workers

    async def font_list(self, build: str) -> FontListReply:
        """Every face we ship as JSON, kept by the browser only when `build` is this one."""
        json = _font_lists.get(BUILD)  # None until the first ask since the server started
        if json is None:
            json = _font_lists[BUILD] = orjson.dumps(await self._enqueue_measure_faces())
        cache = FONT_LIST_CACHE if build == BUILD else "no-store"
        return FontListReply(json, {"Cache-Control": cache})

    @staticmethod
    def measure_faces() -> FontList:
        """Every face we ship, grouped by family, in the catalog's order.

        Runs in a worker, so it's a staticmethod the worker can import by name.
        """
        families: dict[str, FamilyInfo] = {}
        for face in CATALOG:
            new_family: FamilyInfo = {
                "family": face.family,
                "category": face.category,
                "license": face.license,
                "same_widths_as": [],
                "faces": [],
            }
            family = families.setdefault(face.family, new_family)
            # A cut can match fonts its family doesn't, as CMBX10 is only the bold.
            listed = family["same_widths_as"]
            listed.extend(name for name in face.same_widths_as if name not in listed)
            family["faces"].append(
                {"name": face.name, "style": face.style, "glyphs": face_widths(face)}
            )
        return {"build": BUILD, "families": list(families.values())}

    async def _enqueue_measure_faces(self) -> FontList:
        """List the fonts on a worker."""
        return await self._workers.run(FONT_LIST_TIMEOUT_S, FontListController.measure_faces)
