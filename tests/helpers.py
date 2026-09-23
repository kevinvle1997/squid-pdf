"""Assertion helpers that name the offending item on failure.

`all(pred(x) for x in xs)` and `any(...)` tell you the check failed, not which
`x` broke it. These do the same check but report which one, and what it was.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable


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
