"""How many uploads each address may start a minute: a flood is refused before it's read.

An IPv6 address counts with the rest of its /64, the block one home or machine
is given: counted apart, each could start its own minute's worth.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from ipaddress import IPv6Address, ip_address, ip_network

from fastapi import Request

from squidpdf.api import constants
from squidpdf.api.errors import RateLimited

__all__ = [
    "RecentUploads",
    "admit_upload",
]

_WINDOW_S = 60.0
_IPV6_BLOCK_BITS = 64  # an IPv6 address's block: the /64 one home or machine is given


@dataclass(slots=True)
class RecentUploads:
    """When each address started its uploads of the last minute, oldest first.

    An address is as `counted_as` gives it: an IPv6 one is its /64.
    """

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
    recent_uploads: RecentUploads = request.app.state.recent_uploads  # made in the lifespan
    # None only under a server that doesn't say who's calling; they then share one count.
    counted_against = counted_as(request.client.host) if request.client is not None else ""
    # Read as a module attribute, so a test can change the limit.
    admitted = recent_uploads.admit(
        counted_against, now=time.monotonic(), limit=constants.UPLOADS_PER_MINUTE
    )
    if not admitted:
        raise RateLimited()


def counted_as(host: str) -> str:
    """What an upload from `host` counts against: an IPv4 address itself, an IPv6 one's /64."""
    try:
        address = ip_address(host)
    except ValueError:  # raised for a name, not an address, as a test client may give
        return host
    # An IPv4 address: itself.
    if not isinstance(address, IPv6Address):
        return str(address)
    # IPv4 written as IPv6, as a server listening on both sees it: itself, not the
    # /64 every IPv4 address written so shares.
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    # An IPv6 address: its block.
    return str(ip_network((address, _IPV6_BLOCK_BITS), strict=False))
