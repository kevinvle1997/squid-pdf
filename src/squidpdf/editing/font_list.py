from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import orjson

from squidpdf.core import BUILD, CATALOG, LogController, LogEvent, Reply, Workers, face_widths
from squidpdf.editing import constants
from squidpdf.editing.types import FamilyInfo, FontList

_log = LogController.for_module(__name__)


@dataclass(slots=True)
class _FontListTask:
    """The running task, not its result, so a request made while it runs waits for that one."""

    measuring: asyncio.Task[bytes] | None = None

    def task(self, start: Callable[[], asyncio.Task[bytes]]) -> asyncio.Task[bytes]:
        if self.measuring is None:
            self.measuring = start()
        return self.measuring

    def forget_if_failed(self, measuring: asyncio.Task[bytes]) -> None:
        failed = measuring.cancelled() or measuring.exception() is not None
        if failed:
            self.measuring = None

    def forget(self) -> None:
        self.measuring = None


_font_list_task = _FontListTask()


@dataclass(frozen=True, slots=True, eq=False)
class FontListController:
    workers: Workers

    async def font_list(self, build: str) -> Reply[bytes]:
        measuring = _font_list_task.task(self._start_measuring)
        # Shielded: a browser that leaves doesn't stop the measuring others wait for.
        font_list_json = await asyncio.shield(measuring)
        cache = constants.FONT_LIST_CACHE if build == BUILD else "no-store"
        return Reply(font_list_json, {"Cache-Control": cache})

    def measure_ahead(self) -> None:
        """A failure is logged, as no request may wait to say so; the next ask tries again."""
        _font_list_task.task(self._start_measuring_ahead)

    def _start_measuring(self) -> asyncio.Task[bytes]:
        measuring = asyncio.create_task(self._font_list_json())
        measuring.add_done_callback(_font_list_task.forget_if_failed)
        return measuring

    def _start_measuring_ahead(self) -> asyncio.Task[bytes]:
        measuring = self._start_measuring()
        measuring.add_done_callback(_log_if_failed)
        return measuring

    async def _font_list_json(self) -> bytes:
        return orjson.dumps(await self._enqueue_measure_faces())

    async def _enqueue_measure_faces(self) -> FontList:
        return await self.workers.run(constants.FONT_LIST_TIMEOUT_S, _measure_faces)


def _log_if_failed(measuring: asyncio.Task[bytes]) -> None:
    if measuring.cancelled():
        return
    failure = measuring.exception()
    if failure is None:
        return
    _log.write(LogEvent.FONT_LIST_FAILED, failure)


def _measure_faces() -> FontList:
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
