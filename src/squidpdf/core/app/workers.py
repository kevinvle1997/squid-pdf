from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Workers(Protocol):
    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task` crosses processes: a function importable by name (a partial), not a lambda."""
        ...
