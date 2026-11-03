"""The meters that say how many documents a browser may open, asked before a file is read.

A server with no metering, as a self-hosted copy, lets every file in (`UNMETERED`). The
hosted one's meter is accounts' (`accounts/metering.py`), handed to the app as it starts.
Each call may read a database: a coroutine makes it in a thread.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class Meter(Protocol):
    """One browser's allowance of documents, as its cookies say who it is."""

    def check(self, digest: str) -> None:
        """Raise a Problem saying why, if the file `digest` is new and none are left."""
        ...

    def count(self, digest: str) -> dict[str, str]:
        """Count the file `digest`, once only; the headers that keep the count, a cookie say."""
        ...


class Meters(Protocol):
    """Where each browser's meter comes from."""

    def meter_for(self, cookies: Mapping[str, str]) -> Meter:
        """The meter of the browser that sent `cookies`."""
        ...


@dataclass(frozen=True, slots=True)
class _Unmetered:
    """A meter that lets every file in and keeps no count."""

    def check(self, digest: str) -> None:
        """Every file is let in."""

    def count(self, digest: str) -> dict[str, str]:
        """Nothing is counted, so nothing is kept."""
        return {}

    def meter_for(self, cookies: Mapping[str, str]) -> Meter:
        """The same for every browser."""
        return self


# A server that meters nothing: one with no accounts, or none configured to.
UNMETERED = _Unmetered()
