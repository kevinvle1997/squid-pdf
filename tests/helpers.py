"""Assertion helpers that leave a human-readable message on failure.

A bare `assert x == y` makes pytest reconstruct a message from the expression;
`all(pred(x) for x in xs)` and `any(...)` do not even give it that — a failure
just reads `assert False`. Every check in this suite goes through one of these
instead, so a failure always says what was expected and what it found.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable

from httpx import Response


def assert_true(condition: bool, message: str) -> None:
    """Assert `condition` holds; `message` says what was expected."""
    assert condition, message


def assert_false(condition: bool, message: str) -> None:
    """Assert `condition` does not hold; `message` says what was expected."""
    assert not condition, message


def assert_equal(actual: object, expected: object, label: str) -> None:
    """Assert `actual == expected`; name what was being compared."""
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def assert_in(item: object, container: Container, label: str) -> None:
    """Assert `item` is in `container`; name what was being checked."""
    assert item in container, f"{label}: {item!r} not found in {container!r}"


def assert_not_in(item: object, container: Container, label: str) -> None:
    """Assert `item` is not in `container`; name what was being checked."""
    assert item not in container, f"{label}: {item!r} unexpectedly found in {container!r}"


def assert_between(value: float, low: float, high: float, label: str) -> None:
    """Assert `low < value < high`; name what was being measured."""
    assert low < value < high, f"{label}: {value!r} not between {low!r} and {high!r}"


def assert_all[T](
    items: Iterable[T],
    predicate: Callable[[T], bool],
    describe: Callable[[T], str] = repr,
) -> None:
    """Assert `predicate` holds for every item; name the ones that don't."""
    bad = [describe(i) for i in items if not predicate(i)]
    assert not bad, f"failed for: {', '.join(bad)}"


def assert_any[T](
    items: Iterable[T],
    predicate: Callable[[T], bool],
    describe: Callable[[T], str] = repr,
) -> None:
    """Assert `predicate` holds for at least one item; list what was checked."""
    items = list(items)
    assert any(predicate(i) for i in items), (
        f"none matched, checked: {', '.join(describe(i) for i in items)}"
    )


def assert_problem(response: Response, type: str, status: int) -> None:
    """Assert `response` is Problem Details of this `type` and status; show what came back."""
    body = response.text
    assert response.status_code == status, (
        f"status: expected {status}, got {response.status_code}: {body}"
    )
    media = response.headers.get("content-type")
    assert media == "application/problem+json", f"content-type: got {media!r}: {body}"
    got = response.json()
    assert got.get("type") == type, f"problem type: expected {type!r}, got {got!r}"
    assert got.get("status") == status, f"problem status: expected {status}, got {got!r}"
    assert got.get("detail"), f"problem detail: expected a sentence, got {got!r}"
