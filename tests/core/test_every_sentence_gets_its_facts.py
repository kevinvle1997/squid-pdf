"""Every Message and Problem in the code is given every fact its sentence's {placeholders} take.

A placeholder with no fact raises KeyError when it's said, inside the error
handler. Read from the code, not run, so it covers what no test reaches. A fact
no placeholder takes is allowed: a Problem sends its facts to the browser as
`params`, and one call gives every way out of an overflow the same facts.

It reads only what is written out. A Message whose key or facts are a variable
(`Message("chosen_unavailable", chosen)`), and a sentence sent unfilled for the
browser to fill (`words.sentence("missing", said_in)`), are checked for their
key alone. A Problem made by a function passed in, not called by name (a row's
maker), is checked as its class's own `__init__` says.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from squidpdf.core import OPTION_KEYS, Problem, words
from tests.core.conftest import placeholders
from tests.helpers import assert_equal

_SRC = Path(__file__).parents[2] / "src"
_NOT_A_FACT = "debug"  # a Problem's technical why, for a developer: never in the sentence
# What makes a Message of a key and its facts: Message itself, and the one maker that types
# its key, as an approximate span's reason.
_MESSAGE_MAKERS = ("Message", "why_approximate")


@dataclass(frozen=True, slots=True)
class _Said:
    """One sentence the code says: its key, the facts it's given, and where."""

    key: str
    facts: frozenset[str] | None  # None: not written out, so only the key is checked
    where: str


def _trees() -> Iterator[tuple[str, ast.Module]]:
    """Every module in src, parsed, with its path for the failure message."""
    for path in sorted(_SRC.rglob("*.py")):
        yield str(path.relative_to(_SRC)), ast.parse(path.read_text(encoding="utf-8"))


def _named(node: ast.expr) -> str | None:
    """The name a call or a base class is written with: `Message`, `words.sentence`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _text(node: ast.expr) -> str | None:
    """A string written out in the code, or None when it's worked out."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_keys(node: ast.expr) -> frozenset[str] | None:
    """The keys of a dict written out in the code, or None when any is worked out."""
    if not isinstance(node, ast.Dict):
        return None
    keys = [_text(key) for key in node.keys if key is not None]
    if None in keys or len(keys) != len(node.keys):  # `**more`, or a key worked out
        return None
    return frozenset(key for key in keys if key is not None)


def _option_keys(node: ast.expr) -> list[str]:
    """`OPTION_KEYS[name].label` as every sentence key it can be."""
    if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Subscript)):
        return []
    if _named(node.value.value) != "OPTION_KEYS":
        return []
    return [getattr(keys, node.attr) for keys in OPTION_KEYS.values()]


def _messages(where: str, tree: ast.Module) -> Iterator[_Said]:
    """Every `Message(...)` in a module, or a maker's, and every `words.sentence("key")`."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        called = _named(node.func)
        first = node.args[0]
        if called == "sentence" and _text(first) is not None:  # sent unfilled, or printed
            yield _Said(str(_text(first)), None, where)
        if called not in _MESSAGE_MAKERS:
            continue
        keys = [_text(first)] if _text(first) is not None else _option_keys(first)
        facts = _dict_keys(node.args[1]) if len(node.args) > 1 else frozenset()
        yield from (_Said(str(key), facts, where) for key in keys)


@dataclass(frozen=True, slots=True)
class _ProblemClass:
    """A class as the code writes it: what a Problem subclass needs read from it."""

    bases: tuple[str, ...]
    type: str | None  # None: it keeps its base's
    facts: frozenset[str] | None  # what its own __init__ passes up; None without one
    where: str


def _problem_classes() -> dict[str, _ProblemClass]:
    """Every class in src, by name, with what a Problem needs read from it."""
    return {
        node.name: _problem_class(where, node)
        for where, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _problem_class(where: str, node: ast.ClassDef) -> _ProblemClass:
    """One class: its bases, the `type` it sets, and the facts its `__init__` passes up."""
    wire_type = None
    facts = None
    for item in node.body:
        # `type = "not_found"`: the Problem's type, and its sentence's key.
        if isinstance(item, ast.Assign) and _named(item.targets[0]) == "type":
            wire_type = _text(item.value)
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            facts = frozenset(_passed_up(item))
    bases = tuple(str(_named(base)) for base in node.bases)
    return _ProblemClass(bases, wire_type, facts, where)


def _passed_up(init: ast.FunctionDef) -> Iterator[str]:
    """The facts an `__init__` hands to `super().__init__(...)`, by name."""
    for node in ast.walk(init):
        # A call to __init__ on something: super(), in every class here.
        if isinstance(node, ast.Call) and _named(node.func) == "__init__":
            yield from (kw.arg for kw in node.keywords if kw.arg and kw.arg != _NOT_A_FACT)


def _problems() -> Iterator[_Said]:
    """Every Problem subclass in src, with the facts each is raised with."""
    classes = _problem_classes()
    raised = _raised_with(classes)
    for name, cls in classes.items():
        wire_type = _type_of(name, classes)
        if wire_type is None:  # not a Problem
            continue
        if cls.facts is not None:  # its own __init__ says what it passes up
            yield _Said(wire_type, cls.facts, cls.where)
            continue
        facts = raised.get(name, [frozenset()])  # .get: never raised with anything
        yield from (_Said(wire_type, each, cls.where) for each in facts)


def _type_of(name: str, classes: dict[str, _ProblemClass]) -> str | None:
    """A class's wire type, its own or the nearest base's; None when it isn't a Problem."""
    if name == Problem.__name__:
        return Problem.type
    # .get: a base from outside src, such as Exception, isn't a Problem.
    cls = classes.get(name)
    if cls is None:
        return None
    through = [_type_of(base, classes) for base in cls.bases]
    inherited = next((each for each in through if each is not None), None)
    if inherited is None:  # no Problem among its bases
        return None
    return cls.type or inherited


def _raised_with(classes: dict[str, _ProblemClass]) -> dict[str, list[frozenset[str]]]:
    """The facts each class with no `__init__` of its own is made with, one set per call."""
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
    """Each sentence said without a fact one of its placeholders takes, or with no sentence."""
    wrong = []
    for each in said:
        if each.key not in words.ENGLISH_SENTENCES:
            wrong.append(f"{each.where}: {each.key!r} has no sentence")
            continue
        wants = placeholders(words.ENGLISH_SENTENCES[each.key])
        lacking = wants - each.facts if each.facts is not None else frozenset()
        if lacking:
            wrong.append(f"{each.where}: {each.key!r} is given no {sorted(lacking)}")
    return sorted(set(wrong))


def test_every_message_is_given_every_fact_its_sentence_takes():
    said = [each for where, tree in _trees() for each in _messages(where, tree)]
    assert_equal(_mismatches(said), [], "Messages and their sentences")
    # The ways out's keys are read off OPTION_KEYS, not written out: a new way of
    # reading them would drop them from the check without a word.
    option_keys = {key for keys in OPTION_KEYS.values() for key in (keys.label, keys.detail)}
    found = {each.key for each in said}
    assert_equal(option_keys - found, set(), "the ways out's sentences the check never read")


def test_every_problem_is_raised_with_every_fact_its_sentence_takes():
    assert_equal(_mismatches(_problems()), [], "Problems and their sentences")
