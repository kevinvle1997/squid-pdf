"""What runs PDF work off the server's own thread.

A Protocol, so controllers can use it without the web framework. `api/pool.py`'s
WorkerPool fits it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Workers(Protocol):
    """Worker processes that run a function and hand back what it returns."""

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, stopped after `timeout` seconds.

        `task` crosses to another process, so it's a function importable by name
        with its arguments bound by `functools.partial`, not a lambda.
        """
        ...
