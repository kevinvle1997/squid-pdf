"""A limit nothing reads looks enforced and isn't, so each name must have a reader."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.helpers import assert_equal

_SRC = Path(__file__).parents[1] / "src"

# Kept before what reads them is built, each with what will read it.
_NOT_READ_YET: dict[str, str] = {}


def _defined(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names += [target.id for target in targets if isinstance(target, ast.Name)]
    return [name for name in names if name.lstrip("_").isupper()]


def _read(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_every_constant_is_read_somewhere():
    modules = sorted(_SRC.rglob("*.py"))
    read = set().union(*(_read(path) for path in modules))
    defined = [
        name for path in modules if path.name == "constants.py" for name in _defined(path)
    ]
    unread = sorted(name for name in defined if name not in read)
    assert_equal(unread, sorted(_NOT_READ_YET), "constants nothing in src reads")
