"""How many uploads each address may start a minute: a flood is refused before it's read."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import Request

from squidpdf.api import constants
from squidpdf.api.errors import RateLimited

__all__ = [
    "RecentUploads",
    "admit_upload",
]

_WINDOW_S = 60.0


@dataclass(slots=True)
class RecentUploads:
    """When each address started its uploads of the last minute, oldest first."""

    started: dict[str, deque[float]] = field(default_factory=dict)
    forgotten_at: float = 0.0  # when addresses quiet for a minute were last dropped

    def admit(self, address: str, *, now: float, limit: int) -> bool:
        """Count an upload from `address` at `now`, unless it started `limit` this minute."""
        since = now - _WINDOW_S
        # Once a minute, drop every address quiet for one, so a flood of them can't pile up.
        if self.forgotten_at < since:
            self.started = {
                key: times for key, times in self.started.items() if times and times[-1] > since
            }
            self.forgotten_at = now
        times = self.started.setdefault(address, deque())
        while times and times[0] <= since:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(now)
        return True


async def admit_upload(request: Request) -> None:
    """Raise rate_limited if this address has started UPLOADS_PER_MINUTE uploads this minute.

    A dependency of the upload route, so it runs before any of the body is
    read. Async, so it runs on the event loop and two uploads never count at once.
    """
    state = request.app.state
    # Kept per app, so each app (a test's too) counts its own; made on the first upload.
    if not hasattr(state, "recent_uploads"):
        state.recent_uploads = RecentUploads()
    # None only under a server that doesn't say who's calling; they then share one count.
    address = request.client.host if request.client is not None else ""
    # Read as a module attribute, so a test can change the limit.
    admitted = state.recent_uploads.admit(
        address, now=time.monotonic(), limit=constants.UPLOADS_PER_MINUTE
    )
    if not admitted:
        raise RateLimited()
