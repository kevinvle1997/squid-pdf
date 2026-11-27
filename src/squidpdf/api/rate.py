"""An IPv6 address counts with its /64: counted apart, each could take its own share."""

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
_IPV6_BLOCK_BITS = 64


@dataclass(slots=True)
class RecentStarts:
    started: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    forgotten_at: float = 0.0
    window_s: float = _WINDOW_S

    def admit(self, counted_against: str, *, now: float, limit: int) -> bool:
        since = now - self.window_s
        # Once a window, drop all quiet for one, so a flood of addresses can't pile up.
        if self.forgotten_at < since:
            self.forget_quiet(now)
        times = self.started.setdefault(counted_against, deque())
        while times and times[0] <= since:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(now)
        return True

    def forget_quiet(self, now: float) -> None:
        since = now - self.window_s
        self.started = {
            key: times for key, times in self.started.items() if times and times[-1] > since
        }
        self.forgotten_at = now


@dataclass(slots=True)
class _Line:
    turns: asyncio.Semaphore
    in_line: int = 0

    def join(self, *, most: int) -> None:
        if self.in_line >= most:
            raise RateLimited()
        self.in_line += 1

    def leave(self) -> bool:
        self.in_line -= 1
        return self.in_line == 0


@dataclass(slots=True)
class Turns:
    lines: dict[str, _Line] = field(default_factory=dict, repr=False)

    @asynccontextmanager
    async def turn(self, address: str, *, at_once: int, waiting: int) -> AsyncIterator[None]:
        line = self.lines.get(address)
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
    workers: Workers
    turns: Turns
    address: str

    async def run[T](self, timeout: float, task: Callable[[], T]) -> T:
        # The timeout starts in the pool: waiting here is the address's own doing.
        async with self.turns.turn(
            self.address, at_once=constants.JOBS_AT_WORK, waiting=constants.JOBS_WAITING
        ):
            return await self.workers.run(timeout, task)


async def admit_upload(request: Request) -> None:
    """Async, so it runs on the event loop and two uploads never count at once."""
    _admit(request, request.app.state.recent_uploads, limit=constants.UPLOADS_PER_MINUTE)


async def admit_link(request: Request) -> None:
    _admit(request, request.app.state.recent_links, limit=constants.LINKS_PER_MINUTE)


async def admit_payment_page(request: Request) -> None:
    recent = request.app.state.recent_payment_pages
    _admit(request, recent, limit=constants.PAYMENT_PAGES_PER_MINUTE)


async def upload_turn(request: Request) -> AsyncIterator[None]:
    uploads: Turns = request.app.state.upload_turns
    async with uploads.turn(
        _address_of(request), at_once=constants.UPLOADS_UNDER_WAY, waiting=0
    ):
        yield


def workers_in_turn(
    request: Request, workers: Annotated[Workers, Depends(pool.current)]
) -> Workers:
    return _WorkersInTurn(workers, request.app.state.job_turns, _address_of(request))


def _admit(request: Request, recent: RecentStarts, *, limit: int) -> None:
    if not recent.admit(_address_of(request), now=time.monotonic(), limit=limit):
        raise RateLimited()


def _address_of(request: Request) -> str:
    # None only under a server that doesn't say who's calling; they then share one count.
    return _counted_as(request.client.host) if request.client is not None else ""


def _counted_as(host: str) -> str:
    try:
        address = ip_address(host)
    except ValueError:
        return host
    if not isinstance(address, IPv6Address):
        return str(address)
    # IPv4 written as IPv6, as a server listening on both sees it: itself, not the
    # /64 every IPv4 address written so shares.
    if address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(ip_network((address, _IPV6_BLOCK_BITS), strict=False))
