"""What runs PDF work away from the server's own thread.

A Protocol, so a feature's controllers can send work there without importing
the web framework. `api/pool.py`'s Pool is the one there is; a test can hand
in its own.
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

        `fn` and its arguments cross to another process: a function the worker
        imports by name (a module's, or a class's staticmethod), and plain data.
        """
        ...
