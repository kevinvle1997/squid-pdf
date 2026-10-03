"""The rules the docstrings state, checked: what may import what, and how a class holds state.

Read from the source, so a rule holds however deep an import hides, even one
inside a function.
"""

from __future__ import annotations

import ast
import builtins
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers import assert_equal

_ROOT = Path(__file__).parents[1]
_SRC = _ROOT / "src"


def _imports(tree: ast.Module) -> set[str]:
    """Every module a file imports, counting `P.N` for each `from P import N`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _module(path: Path) -> str:
    """`src/squidpdf/core/pdf/lowlevel.py` -> `squidpdf.core.pdf.lowlevel`."""
    parts = path.relative_to(_SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


_TREES = {
    _module(path): ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(_SRC.rglob("*.py"))
}
_MODULES = {module: _imports(tree) for module, tree in _TREES.items()}

# The web layer: the `api` extra is optional, so the CLI and pool work must run without it.
_WEB = ["squidpdf.api", "squidpdf.documents.api", "squidpdf.editing.api"]


def _within(name: str, package: str) -> bool:
    """Whether module `name` is `package` or inside it."""
    return name == package or name.startswith(package + ".")


@pytest.mark.parametrize(
    ("importers", "imported", "allowed"),
    [
        ("squidpdf", "pymupdf", ["squidpdf.core.pdf"]),
        ("squidpdf", "fitz", []),
        ("squidpdf.core", "squidpdf.editing", []),
        ("squidpdf.core", "squidpdf.documents", []),
        ("squidpdf.core", "squidpdf.api", []),
        ("squidpdf.core", "squidpdf.cli", []),
        ("squidpdf.documents", "squidpdf.editing", []),
        ("squidpdf.core.text", "squidpdf.core.fonts", []),
        ("squidpdf", "squidpdf.api", _WEB),
        ("squidpdf", "squidpdf.documents.api", _WEB),
        ("squidpdf", "squidpdf.editing.api", _WEB),
        ("squidpdf", "fastapi", _WEB),
        ("squidpdf", "starlette", _WEB),
        ("squidpdf", "pydantic", _WEB),
    ],
    ids=[
        "only core/pdf/ talks to MuPDF",
        "nothing reaches MuPDF by its old name, around the rule above",
        "core imports no feature: editing",
        "core imports no feature: documents",
        "core imports no feature: the web layer",
        "core imports no feature: the CLI",
        "editing builds on documents, never the other way",
        "core's fonts build on its text, never the other way",
        "the web layer stays in api/ and each feature's api.py, so the CLI runs without it",
        "only the web layer imports documents' routes",
        "only the web layer imports editing's routes",
        "only the web layer imports FastAPI",
        "only the web layer imports Starlette",
        "only the web layer imports Pydantic",
    ],
)
def test_the_import_rule_holds(importers, imported, allowed):
    breaking = [
        module
        for module, imports in _MODULES.items()
        if _within(module, importers)
        and not any(_within(module, ok) for ok in allowed)
        and any(_within(name, imported) for name in imports)
    ]
    assert_equal(breaking, [], f"modules in {importers} importing {imported}")


# Core's own modules and packages: outside core, `squidpdf.core` is the only one to import.
_CORE_INSIDE = [name for name in _MODULES if name.startswith("squidpdf.core.")]


def _past_core_door(name: str) -> bool:
    """Whether an imported name is one of core's modules, or inside one."""
    return any(_within(name, inside) for inside in _CORE_INSIDE)


def test_outside_core_squidpdf_core_is_the_only_way_in():
    """A feature imports `from squidpdf.core import ...`, never one of core's modules.

    So core can move its insides without touching a feature, what a feature uses
    is exported on purpose, and nothing outside names the driver: `open_pdf` is
    the way in. `from squidpdf.core import Span` reads as `squidpdf.core.Span`,
    which is no module, so only an import past the door counts.
    """
    breaking = [
        module
        for module, imports in _MODULES.items()
        if not _within(module, "squidpdf.core")
        and any(_past_core_door(name) for name in imports)
    ]
    assert_equal(breaking, [], "modules outside core importing one of core's modules")


def test_nothing_inside_imports_the_package_itself():
    """`squidpdf` re-exports editing for outside callers: core importing it imports a feature.

    The rules above can't see it: `from squidpdf import Edit` names no feature.
    """
    breaking = [module for module, imports in _MODULES.items() if "squidpdf" in imports]
    assert_equal(breaking, [], "modules importing the squidpdf package itself")


# How a class holds its state (rules/readable.md, Data): in public fields, set when it's
# made, and changed later only by a holder of changing state, through its own methods.

_EXCEPTIONS_BUILT_IN = {
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
}
# Where an object's fields are set as it's made, so setting them there isn't changing them.
_MAKERS = ("__init__", "__post_init__")


def _classes() -> Iterator[tuple[str, ast.ClassDef]]:
    """Every class in src, with the module it's in."""
    for module, tree in _TREES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                yield module, node


def _base_name(base: ast.expr) -> str:
    """The name a base goes by: `Problem`, or `FileDataError` for `pymupdf.FileDataError`."""
    if isinstance(base, ast.Attribute):
        return base.attr
    return base.id if isinstance(base, ast.Name) else ""


def _exceptions() -> set[str]:
    """Every exception class in src by name, and Python's own, whatever it derives from."""
    names = set(_EXCEPTIONS_BUILT_IN)
    classes = [node for _module, node in _classes()]
    while True:
        derived = {
            node.name for node in classes if any(_base_name(b) in names for b in node.bases)
        }
        if derived <= names:
            return names
        names |= derived


def _set_on_self(method: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[str]:
    """Each field a method sets on `self`, by name, in tuples and nested functions too."""
    for node in ast.walk(method):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        while targets:
            target = targets.pop()
            if isinstance(target, (ast.Tuple, ast.List)):
                targets.extend(target.elts)
            elif isinstance(target, ast.Starred):
                targets.append(target.value)
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                if target.value.id == "self":
                    yield target.attr


def _fields_set(node: ast.ClassDef) -> Iterator[tuple[str, str]]:
    """Each field a class's own methods set on `self`, and the method that sets it."""
    for method in node.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name in _set_on_self(method):
                yield method.name, name


def _is_holder(node: ast.ClassDef) -> bool:
    """Whether a class is `@dataclass(slots=True)`, not frozen: a holder of changing state."""
    for decorator in node.decorator_list:
        # A bare `@dataclass`, or another decorator: no options to read.
        if not isinstance(decorator, ast.Call) or _base_name(decorator.func) != "dataclass":
            continue
        options = {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords}
        # .get: an option left out takes dataclass's default, False for both.
        return options.get("slots") is True and options.get("frozen") is not True
    return False


def test_no_class_holds_its_state_in_a_private_attribute():
    """`self._x` hides what an object holds: a reader would search every method to learn it."""
    breaking = [
        f"{module}.{node.name}.{method}: self.{name}"
        for module, node in _classes()
        for method, name in _fields_set(node)
        if name.startswith("_")
    ]
    assert_equal(breaking, [], "private attributes set in src")


def test_only_an_exception_sets_its_fields_in_init():
    """Any other is a dataclass: its fields at the top, set whole as it's made, never after."""
    exceptions = _exceptions()
    breaking = [
        f"{module}.{node.name}: self.{name}"
        for module, node in _classes()
        if node.name not in exceptions
        for method, name in _fields_set(node)
        if method == "__init__"
    ]
    assert_equal(breaking, [], "classes, not exceptions, that set fields in __init__")


def test_only_a_holder_changes_its_fields_after_it_is_made():
    """A class made of parts is frozen; one that changes is `@dataclass(slots=True)`."""
    breaking = [
        f"{module}.{node.name}.{method}: self.{name}"
        for module, node in _classes()
        if not _is_holder(node)
        for method, name in _fields_set(node)
        if method not in _MAKERS
    ]
    assert_equal(breaking, [], "fields changed after an object is made, outside a holder")


# Where a table lives (rules/backend/python.md, Constants): several modules read it, so it's
# in a constants.py, or one module does, so it's private to it. These live elsewhere on
# purpose, by name, each with why.
_TABLES_ELSEWHERE = {
    "QUARTER_TURNS": "type data several modules read, beside its type, QuarterTurn",
    "CATALOG": "the faces we ship: data about files, not tuning, in core/fonts/catalog.py",
    "FACES": "the faces we ship, by name, beside CATALOG",
}


def _is_table(value: ast.expr) -> bool:
    """Whether a value is written as a table: a dict, or a tuple or list of rows.

    A row is a tuple or a call (`Failure(...)`), spread with `*` or not. A short
    tuple of literals (`("CFF ", "CFF2")`) is a value, not a table.
    """
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if not isinstance(value, (ast.Tuple, ast.List)):
        return False
    items = [item.value if isinstance(item, ast.Starred) else item for item in value.elts]
    return any(isinstance(item, (ast.Tuple, ast.Call)) for item in items)


def _assignments(tree: ast.Module) -> Iterator[tuple[ast.expr, ast.expr]]:
    """Each `NAME = value` or `NAME: type = value` at a module's top level: target, value."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            yield node.targets[0], node.value
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            yield node.target, node.value


def _tables() -> Iterator[tuple[str, str]]:
    """Each table a module outside a constants.py names at its top level: module, name."""
    for module, tree in _TREES.items():
        if module.endswith(".constants"):
            continue
        for target, value in _assignments(tree):
            if isinstance(target, ast.Name) and _is_table(value):
                yield module, target.id


def test_a_table_outside_a_constants_py_is_private_unless_named():
    """A table several modules read goes in a constants.py; one module's stays private to it.

    Any name counts, not only an UPPER_SNAKE one: lower case doesn't make a table less of
    one, and a dict that functions fill is a holder's job (rules/backend/python.md, Data).
    """
    tables = list(_tables())
    breaking = [
        f"{module}.{name}"
        for module, name in tables
        if not name.startswith("_") and name not in _TABLES_ELSEWHERE
    ]
    assert_equal(breaking, [], "public tables outside a constants.py")
    allowed_unused = set(_TABLES_ELSEWHERE) - {name for _module, name in tables}
    assert_equal(allowed_unused, set(), "tables let through by name that are no longer tables")


# Where a requirement's name ends: its extras, version, or environment marker begin.
_NAME_ENDS = re.compile(r"[\[<>=~!; ]")


def _api_extra() -> list[str]:
    """What the `api` extra installs, by import name: `uvicorn[standard]>=0.32` is `uvicorn`."""
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["project"]["optional-dependencies"]["api"]
    return [_NAME_ENDS.split(requirement, maxsplit=1)[0] for requirement in requirements]


# FastAPI's own two: they come with it, so the extra doesn't name them.
_WEB_FRAMEWORK = ["starlette", "pydantic"]

# Run in a fresh interpreter, so nothing the suite imported already hides a missing package.
# A module set to None in sys.modules raises ImportError when anything imports it.
_IMPORT_BLOCKED = """
import sys
sys.modules.update(dict.fromkeys(sys.argv[1:]))
import squidpdf.cli
"""


def test_the_cli_runs_without_the_api_extra():
    """The rules above read the source; this imports it, so a package they don't name counts."""
    blocked = _api_extra() + _WEB_FRAMEWORK
    ran = subprocess.run(
        [sys.executable, "-c", _IMPORT_BLOCKED, *blocked],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_equal(ran.stderr, "", f"importing the CLI with {', '.join(blocked)} blocked")
