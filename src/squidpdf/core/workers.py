"""What runs PDF work off the server's own thread.

A Protocol, so controllers can use it without the web framework. `api/pool.py`'s Pool fits it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Workers(Protocol):
    """Worker processes that run a function and hand back what it returns."""

    async def run[**P, T](
        self, timeout: float, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """`fn(*args, **kwargs)` in a worker, stopped after `timeout` seconds.

        `fn` must be importable by name, and its arguments plain data: they cross processes.
        """
        ...
