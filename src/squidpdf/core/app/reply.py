"""What a controller hands its route to send: the body, its headers and its status.

Framework-free, so a controller can build one without the web framework and the
route turns it into a response in one line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus


@dataclass(frozen=True, slots=True)
class Reply[T]:
    """A reply ready to send: `body` is the bytes, a file to stream, or the data written out."""

    body: T = field(repr=False)  # a PNG, an open PDF, or a whole JSON body
    headers: dict[str, str]
    status: int = HTTPStatus.OK
