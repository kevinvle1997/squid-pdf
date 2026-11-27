from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class Reply[T]:
    body: T = field(repr=False)
    headers: dict[str, str]
    status: int = HTTPStatus.OK
