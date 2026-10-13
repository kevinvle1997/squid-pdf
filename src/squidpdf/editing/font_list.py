"""The font list: every face new text can be drawn in, with its widths. No web framework."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import orjson

# Not through `Engine`: these are our own files, with no document to open.
from squidpdf.core import BUILD, CATALOG, Reply, Workers, face_widths
from squidpdf.editing.constants import FONT_LIST_CACHE, FONT_LIST_TIMEOUT_S
from squidpdf.editing.types import FamilyInfo, FontList


@dataclass(slots=True)
class _FontListTask:
    """The font list's measuring, running or done: made once per server.

    The same for everyone under this build, and seconds of pool work. The running
    task, not its result, so a request that comes while it runs waits for that one.
    """

    measuring: asyncio.Task[bytes] | None = None  # None: not started, or forgotten

    def task(self, start: Callable[[], asyncio.Task[bytes]]) -> asyncio.Task[bytes]:
        """The measuring, started on the first ask since none was kept."""
        if self.measuring is None:
            self.measuring = start()
        return self.measuring

    def forget_if_failed(self, measuring: asyncio.Task[bytes]) -> None:
        """Drop `measuring`, done, if it failed, so the next request tries again."""
        failed = measuring.cancelled() or measuring.exception() is not None
        if failed:
            self.measuring = None

    def forget(self) -> None:
        """Forget the measuring, as a fresh record."""
        self.measuring = None


# This server's font list, measured or being measured.
_font_list_task = _FontListTask()


@dataclass(frozen=True, slots=True, eq=False)
class FontListController:
    """The font list, from request to reply."""

    workers: Workers  # where it lists the fonts, off the server's own thread

    async def font_list(self, build: str) -> Reply[bytes]:
        """Every face we ship as JSON, kept by the browser only when `build` is this one."""
        # Started on the first ask since the server started, or since the last one failed.
        measuring = _font_list_task.task(self._start_measuring)
        # Shielded: a browser that leaves doesn't stop the measuring others wait for.
        font_list_json = await asyncio.shield(measuring)
        cache = FONT_LIST_CACHE if build == BUILD else "no-store"
        return Reply(font_list_json, {"Cache-Control": cache})

    def _start_measuring(self) -> asyncio.Task[bytes]:
        """Start measuring the font list; it's dropped if it fails."""
        measuring = asyncio.create_task(self._font_list_json())
        measuring.add_done_callback(_font_list_task.forget_if_failed)
        return measuring

    async def _font_list_json(self) -> bytes:
        """The font list, measured on a worker, as the JSON every browser gets."""
        return orjson.dumps(await self._enqueue_measure_faces())

    async def _enqueue_measure_faces(self) -> FontList:
        """List the fonts on a worker."""
        return await self.workers.run(FONT_LIST_TIMEOUT_S, _measure_faces)


def _measure_faces() -> FontList:
    """Every face we ship, grouped by family, in the catalog's order. Runs in a worker."""
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
