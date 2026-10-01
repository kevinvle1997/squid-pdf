"""Every job a controller sends to the pool pickles: a module function, never a lambda.

The pool runs each job in another process, so the job crosses as a pickle,
by its module and name (rules/backend/api.md). A lambda or a closure can't,
and fails only when a request reaches it. Read from the source: each
`_enqueue_<job>` method names its job, in a `functools.partial` or as it is.
"""

from __future__ import annotations

import ast
import importlib
import pickle
from collections.abc import Iterator
from pathlib import Path

from tests.helpers import assert_equal, assert_true

_SRC = Path(__file__).parents[1] / "src"
_ENQUEUE = "_enqueue_"


def _module(path: Path) -> str:
    """`src/squidpdf/editing/render.py` -> `squidpdf.editing.render`."""
    return ".".join(path.relative_to(_SRC).with_suffix("").parts)


def _enqueuers() -> Iterator[tuple[str, ast.AsyncFunctionDef]]:
    """Each `_enqueue_<job>` method in src, with the module it's in."""
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith(_ENQUEUE):
                yield _module(path), node


def _job_name(enqueuer: ast.AsyncFunctionDef) -> str | None:
    """The job a method sends: `partial`'s first argument, else `run`'s; None if neither."""
    calls = [node for node in ast.walk(enqueuer) if isinstance(node, ast.Call)]
    for call in calls:
        is_partial = isinstance(call.func, ast.Name) and call.func.id == "partial"
        if is_partial and isinstance(call.args[0], ast.Name):
            return call.args[0].id
    for call in calls:
        is_run = isinstance(call.func, ast.Attribute) and call.func.attr == "run"
        if is_run and isinstance(call.args[-1], ast.Name) and call.args[-1].id != "task":
            return call.args[-1].id
    return None


def test_every_pool_job_is_a_module_function_that_pickles():
    enqueuers = list(_enqueuers())
    assert_true(bool(enqueuers), "no _enqueue_ methods found: has the convention moved?")
    for module_name, enqueuer in enqueuers:
        where = f"{module_name}.{enqueuer.name}"
        name = _job_name(enqueuer)
        assert_true(name is not None, f"{where} sends no job by name")
        job = getattr(importlib.import_module(module_name), str(name))
        assert_equal(pickle.loads(pickle.dumps(job)), job, f"{where}'s job after a pickle")
