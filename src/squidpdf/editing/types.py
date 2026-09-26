"""Shapes editing shares between the log, the fit check and what it reports back."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
