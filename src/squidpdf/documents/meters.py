from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class Meter(Protocol):
    def check(self, digest: str) -> None: ...

    def count(self, digest: str) -> dict[str, str]: ...


class Meters(Protocol):
    def meter_for(self, cookies: Mapping[str, str]) -> Meter: ...


@dataclass(frozen=True, slots=True)
class _Unmetered:
    def check(self, digest: str) -> None: ...

    def count(self, digest: str) -> dict[str, str]:
        return {}

    def meter_for(self, cookies: Mapping[str, str]) -> Meter:
        return self


UNMETERED = _Unmetered()
