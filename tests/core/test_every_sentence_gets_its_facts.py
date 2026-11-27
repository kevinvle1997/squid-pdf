"""Read from the code, not run: a missing fact raises KeyError only when said."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from squidpdf.core import OPTION_KEYS, Problem, words
from tests.core.conftest import placeholders
from tests.helpers import assert_equal

_SRC = Path(__file__).parents[2] / "src"
_NOT_A_FACT = "debug"
_MESSAGE_MAKERS = ("Message", "why_approximate")


@dataclass(frozen=True, slots=True)
class _Said:
    key: str
    facts: frozenset[str] | None
    where: str


def _trees() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(_SRC.rglob("*.py")):
        yield str(path.relative_to(_SRC)), ast.parse(path.read_text(encoding="utf-8"))


def _named(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_keys(node: ast.expr) -> frozenset[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys = [_text(key) for key in node.keys if key is not None]
    if None in keys or len(keys) != len(node.keys):
        return None
    return frozenset(key for key in keys if key is not None)


def _option_keys(node: ast.expr) -> list[str]:
    if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Subscript)):
        return []
    if _named(node.value.value) != "OPTION_KEYS":
        return []
    return [getattr(keys, node.attr) for keys in OPTION_KEYS.values()]


def _messages(where: str, tree: ast.Module) -> Iterator[_Said]:
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        called = _named(node.func)
        first = node.args[0]
        if called == "sentence" and _text(first) is not None:
            yield _Said(str(_text(first)), None, where)
        if called not in _MESSAGE_MAKERS:
            continue
        keys = [_text(first)] if _text(first) is not None else _option_keys(first)
        facts = _dict_keys(node.args[1]) if len(node.args) > 1 else frozenset()
        yield from (_Said(str(key), facts, where) for key in keys)


@dataclass(frozen=True, slots=True)
class _ProblemClass:
    bases: tuple[str, ...]
    type: str | None
    facts: frozenset[str] | None
    where: str


def _problem_classes() -> dict[str, _ProblemClass]:
    return {
        node.name: _problem_class(where, node)
        for where, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _problem_class(where: str, node: ast.ClassDef) -> _ProblemClass:
    wire_type = None
    facts = None
    for item in node.body:
        if isinstance(item, ast.Assign) and _named(item.targets[0]) == "type":
            wire_type = _text(item.value)
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            facts = frozenset(_passed_up(item))
    bases = tuple(str(_named(base)) for base in node.bases)
    return _ProblemClass(bases, wire_type, facts, where)


def _passed_up(init: ast.FunctionDef) -> Iterator[str]:
    for node in ast.walk(init):
        if isinstance(node, ast.Call) and _named(node.func) == "__init__":
            yield from (kw.arg for kw in node.keywords if kw.arg and kw.arg != _NOT_A_FACT)


def _problems() -> Iterator[_Said]:
    classes = _problem_classes()
    raised = _raised_with(classes)
    for name, cls in classes.items():
        wire_type = _type_of(name, classes)
        if wire_type is None:
            continue
        if cls.facts is not None:
            yield _Said(wire_type, cls.facts, cls.where)
            continue
        facts = raised.get(name, [frozenset()])
        yield from (_Said(wire_type, each, cls.where) for each in facts)


def _type_of(name: str, classes: dict[str, _ProblemClass]) -> str | None:
    if name == Problem.__name__:
        return Problem.type
    cls = classes.get(name)
    if cls is None:
        return None
    through = [_type_of(base, classes) for base in cls.bases]
    inherited = next((each for each in through if each is not None), None)
    if inherited is None:
        return None
    return cls.type or inherited


def _raised_with(classes: dict[str, _ProblemClass]) -> dict[str, list[frozenset[str]]]:
    made: dict[str, list[frozenset[str]]] = {}
    for _where, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _named(node.func)
            known = name in classes and classes[str(name)].facts is None
            if known:
                facts = frozenset(kw.arg for kw in node.keywords if kw.arg != _NOT_A_FACT)
                made.setdefault(str(name), []).append(frozenset(str(fact) for fact in facts))
    return made


def _mismatches(said: Iterable[_Said]) -> list[str]:
    wrong = []
    for each in said:
        if each.key not in words.CATALOGS[words.ENGLISH]:
            wrong.append(f"{each.where}: {each.key!r} has no sentence")
            continue
        wants = placeholders(words.CATALOGS[words.ENGLISH][each.key])
        lacking = wants - each.facts if each.facts is not None else frozenset()
        if lacking:
            wrong.append(f"{each.where}: {each.key!r} is given no {sorted(lacking)}")
    return sorted(set(wrong))


def test_every_message_is_given_every_fact_its_sentence_takes():
    said = [each for where, tree in _trees() for each in _messages(where, tree)]
    assert_equal(_mismatches(said), [], "Messages and their sentences")
    # Read off OPTION_KEYS: a new way of reading them would drop them from the check silently.
    option_keys = {key for keys in OPTION_KEYS.values() for key in (keys.label, keys.detail)}
    found = {each.key for each in said}
    assert_equal(option_keys - found, set(), "the ways out's sentences the check never read")


def test_every_problem_is_raised_with_every_fact_its_sentence_takes():
    assert_equal(_mismatches(_problems()), [], "Problems and their sentences")
