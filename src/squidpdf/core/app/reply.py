"""What a controller hands its route to send: the body, its headers and its status.

Framework-free, so a controller can build one without the web framework and the
route turns it into a response in one line.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class Reply[T]:
    """A reply ready to send: `body` is the bytes, or the data the framework writes out."""

    body: T
    headers: dict[str, str]
    status: int = HTTPStatus.OK
