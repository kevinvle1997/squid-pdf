"""A bare `assert` over `all(...)` fails as just `assert False`: these say what was found."""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable
from dataclasses import fields
from typing import TYPE_CHECKING

from httpx import Response

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def assert_true(condition: bool, message: str) -> None:
    assert condition, message


def assert_false(condition: bool, message: str) -> None:
    assert not condition, message


def assert_equal(actual: object, expected: object, label: str) -> None:
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def assert_in(item: object, container: Container, label: str) -> None:
    assert item in container, f"{label}: {item!r} not found in {container!r}"


def assert_not_in(item: object, container: Container, label: str) -> None:
    assert item not in container, f"{label}: {item!r} unexpectedly found in {container!r}"


def assert_at_most(value: float, most: float, label: str) -> None:
    assert value <= most, f"{label}: expected at most {most!r}, got {value!r}"


def assert_at_least(value: float, least: float, label: str) -> None:
    assert value >= least, f"{label}: expected at least {least!r}, got {value!r}"


def assert_close(actual: float, expected: float, within: float, label: str) -> None:
    off = abs(actual - expected)
    assert off <= within, f"{label}: expected {expected!r} within {within!r}, got {actual!r}"


def assert_every_field_filled(holder: DataclassInstance, fresh: object, label: str) -> None:
    unfilled = [
        f.name for f in fields(holder) if getattr(holder, f.name) == getattr(fresh, f.name)
    ]
    assert not unfilled, f"{label}: fields still as a fresh one has them: {', '.join(unfilled)}"


def assert_all[T](
    items: Iterable[T],
    predicate: Callable[[T], bool],
    describe: Callable[[T], str] = repr,
) -> None:
    bad = [describe(i) for i in items if not predicate(i)]
    assert not bad, f"failed for: {', '.join(bad)}"


def assert_problem(response: Response, problem_type: str, status: int) -> None:
    body = response.text
    assert response.status_code == status, (
        f"status: expected {status}, got {response.status_code}: {body}"
    )
    media = response.headers.get("content-type")
    assert media == "application/problem+json", f"content-type: got {media!r}: {body}"
    got = response.json()
    assert got.get("type") == problem_type, (
        f"problem type: expected {problem_type!r}, got {got!r}"
    )
    assert got.get("status") == status, f"problem status: expected {status}, got {got!r}"
    assert got.get("detail"), f"problem detail: expected a sentence, got {got!r}"
