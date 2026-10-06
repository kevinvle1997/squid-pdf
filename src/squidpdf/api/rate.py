"""What each address may have going: uploads a minute, uploads at once, jobs at once.

Each is checked before any of the body is read. An IPv6 address counts with the
rest of its /64, the block one home or machine is given: counted apart, each
could take its own share.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from ipaddress import IPv6Address, ip_address, ip_network
from typing import Annotated

from fastapi import Depends, Request

from squidpdf.api import constants, pool
from squidpdf.api.errors import RateLimited
from squidpdf.core import Workers

_WINDOW_S = 60.0
_IPV6_BLOCK_BITS = 64  # an IPv6 address's block: the /64 one home or machine is given


@dataclass(slots=True)
class RecentUploads:
    """When each address started its uploads of the last minute, oldest first.

    An address is as `_counted_as` gives it: an IPv6 one is its /64.
    """

    started: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    forgotten_at: float = 0.0  # when addresses quiet for a minute were last dropped

    def admit(self, address: str, *, now: float, limit: int) -> bool:
        """Count an upload from `address` at `now`, unless it started `limit` this minute."""
        since = now - _WINDOW_S
        # Once a minute, drop every address quiet for one, so a flood of them can't pile up.
        if self.forgotten_at < since:
            self.forget_quiet(now)
        times = self.started.setdefault(address, deque())
        while times and times[0] <= since:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(now)
        return True

    def forget_quiet(self, now: float) -> None:
        """Drop every address that started no upload in the minute before `now`."""
        since = now - _WINDOW_S
        self.started = {
            key: times for key, times in self.started.items() if times and times[-1] > since
        }
        self.forgotten_at = now


@dataclass(slots=True)
class _Line:
    """One address's requests of one kind: some taking their turn, the rest waiting for one."""

    turns: asyncio.Semaphore  # one per request taking its turn
    in_line: int = 0  # taking their turn and waiting

    def join(self, *, most: int) -> None:
        """Join the line, or raise RateLimited when `most` are in it already."""
        if self.in_line >= most:
            raise RateLimited()
        self.in_line += 1

    def leave(self) -> bool:
        """Leave the line; whether it's empty now."""
        self.in_line -= 1
        return self.in_line == 0


@dataclass(slots=True)
class Turns:
    """Each address's requests of one kind, taking their turn or waiting for one.

    An address with none has no line, so a flood of addresses leaves nothing behind.
    """

    lines: dict[str, _Line] = field(default_factory=dict, repr=False)

    @asynccontextmanager
    async def turn(self, address: str, *, at_once: int, waiting: int) -> AsyncIterator[None]:
        """Hold one of `address`'s `at_once` turns while the block runs, waiting for one.

        Raises RateLimited, at once, when `waiting` wait already. On the event loop, so two
        requests never take one place.
        """
        line = self.lines.get(address)  # none while the address has nothing in line
        if line is None:
            line = self.lines[address] = _Line(asyncio.Semaphore(at_once))
        line.join(most=at_once + waiting)
        try:
            async with line.turns:
                yield
        finally:
            if line.leave():
                del self.lines[address]


@dataclass(frozen=True, slots=True, eq=False)
class _WorkersInTurn:
    """The app's workers, each job a request sends waiting for one of its address's turns."""

    workers: Workers  # the app's
    turns: Turns  # the app's turns at a job
    address: str  # the request's, as `_counted_as` gives it

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        """`task()` in a worker, once the address has a turn; past JOBS_WAITING, refused."""
        # The timeout starts in the pool: waiting here is the address's own doing.
        async with self.turns.turn(
            self.address, at_once=constants.JOBS_AT_WORK, waiting=constants.JOBS_WAITING
        ):
            return await self.workers.run(timeout, task)


async def admit_upload(request: Request) -> None:
    """Raise rate_limited if this address has started UPLOADS_PER_MINUTE uploads this minute.

    A dependency of the upload route, so it runs before any of the body is
    read. Async, so it runs on the event loop and two uploads never count at once.
    """
    recent_uploads: RecentUploads = request.app.state.recent_uploads  # made in the lifespan
    # Read as a module attribute, so a test can change the limit.
    admitted = recent_uploads.admit(
        _address_of(request), now=time.monotonic(), limit=constants.UPLOADS_PER_MINUTE
    )
    if not admitted:
        raise RateLimited()


async def upload_turn(request: Request) -> AsyncIterator[None]:
    """Hold one of this address's turns at an upload, a PDF or a font's copy, until answered.

    Raises rate_limited past UPLOADS_UNDER_WAY: a trickled upload holds disk against the
    floor, so one address can't hold all of it. Nothing waits: a browser hears at once.
    """
    uploads: Turns = request.app.state.upload_turns  # made in the lifespan
    async with uploads.turn(
        _address_of(request), at_once=constants.UPLOADS_UNDER_WAY, waiting=0
    ):
        yield


def workers_in_turn(
    request: Request, workers: Annotated[Workers, Depends(pool.current)]
) -> Workers:
    """The app's workers, as a request's controller sends them jobs: one turn a job.

    Past JOBS_AT_WORK an address's jobs wait, holding no worker, so one address can't hold
    every worker. They wait, not refused, since the page images near the view ask at once.
    """
    return _WorkersInTurn(workers, request.app.state.job_turns, _address_of(request))


def _address_of(request: Request) -> str:
    """What a request counts against: its address, an IPv6 one's /64."""
    # None only under a server that doesn't say who's calling; they then share one count.
    return _counted_as(request.client.host) if request.client is not None else ""


def _counted_as(host: str) -> str:
    """What a request from `host` counts against: an IPv4 address itself, an IPv6 one's /64."""
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
