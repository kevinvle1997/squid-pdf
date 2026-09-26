"""Shapes editing shares between the log, the fit check and what render sends back.

TypedDicts for the JSON, as in `documents/types.py`; no framework, so the pool
can import them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

# How a too-long replacement is drawn; the names the user's options go by.
type Strategy = Literal["as-is", "shrink", "condense"]


@dataclass(frozen=True, slots=True)
class Skipped:
    """An edit left out because what it points at isn't in the document.

    `edit` is its position in the list the browser sent.
    """

    edit: int
    type: str
    detail: str


@dataclass(frozen=True, slots=True)
class Region:
    """What to draw: a full-width strip of a page, from `y0` to `y1` in points, or all of it."""

    page: int
    y0: float | None = None
    y1: float | None = None


class ImageInfo(TypedDict):
    """A drawn strip, its top `y` in points on the page, as a base64 PNG."""

    page: int
    y: float
    image: str


class FitInfo(TypedDict):
    """Whether a replacement fits, the ways out if not, and which one was drawn."""

    delta_pt: float
    missing: list[str]
    options: list[Strategy]
    strategy: Strategy
    message: str | None


class Rendered(TypedDict):
    """What render worked out: the strips, a fit per replaced span, and what it skipped."""

    images: list[ImageInfo]
    fits: dict[str, FitInfo]
    redactions: list[dict[str, str]]
    skipped: list[Skipped]


class Render(Rendered):
    """Render's reply, as the browser gets it."""

    build: str
    expires_at: str
