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
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _module(path: Path) -> str:
    parts = path.relative_to(_SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


_TREES = {
    _module(path): ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(_SRC.rglob("*.py"))
}
_MODULES = {module: _imports(tree) for module, tree in _TREES.items()}

# The web layer: the `api` extra is optional, so the CLI and pool work must run without it.
_WEB = [
    "squidpdf.api",
    "squidpdf.documents.api",
    "squidpdf.editing.api",
    "squidpdf.terms.api",
    "squidpdf.accounts.api",
]


def _within(name: str, package: str) -> bool:
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
        ("squidpdf.core", "squidpdf.terms", []),
        ("squidpdf.core", "squidpdf.accounts", []),
        ("squidpdf.documents", "squidpdf.editing", []),
        ("squidpdf.documents", "squidpdf.accounts", []),
        ("squidpdf", "squidpdf.api", _WEB),
        ("squidpdf", "squidpdf.documents.api", _WEB),
        ("squidpdf", "squidpdf.editing.api", _WEB),
        ("squidpdf", "squidpdf.terms.api", _WEB),
        ("squidpdf", "squidpdf.accounts.api", _WEB),
        ("squidpdf", "fastapi", _WEB),
        ("squidpdf", "starlette", _WEB),
        ("squidpdf", "pydantic", _WEB),
        ("squidpdf", "logging", ["squidpdf.core.app.logs"]),
    ],
    ids=[
        "only core/pdf/ talks to MuPDF",
        "nothing reaches MuPDF by its old name, around the rule above",
        "core imports no feature: editing",
        "core imports no feature: documents",
        "core imports no feature: the web layer",
        "core imports no feature: the CLI",
        "core imports no feature: terms",
        "core imports no feature: accounts",
        "editing builds on documents, never the other way",
        "documents meters through a Meter it's handed, never accounts",
        "the web layer stays in api/ and each feature's api.py, so the CLI runs without it",
        "only the web layer imports documents' routes",
        "only the web layer imports editing's routes",
        "only the web layer imports the terms' route",
        "only the web layer imports accounts' routes",
        "only the web layer imports FastAPI",
        "only the web layer imports Starlette",
        "only the web layer imports Pydantic",
        "only LogController logs, so every line has its shape and no document",
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


def test_only_logcontroller_calls_the_event_loops_exception_handler():
    breaking = [
        module
        for module, tree in _TREES.items()
        if module != "squidpdf.core.app.logs"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "call_exception_handler"
    ]
    assert_equal(breaking, [], "modules calling the event loop's exception handler")


def _in_body(function: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    waiting: list[ast.AST] = list(function.body)
    while waiting:
        node = waiting.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        waiting.extend(ast.iter_child_nodes(node))


_DISK_MODULES = ("store", "attached")
_STORE_OBJECT = "documents"


def _disk_name_called(node: ast.AST) -> str | None:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    called = node.func
    holder = called.value
    if isinstance(holder, ast.Name) and holder.id in (*_DISK_MODULES, _STORE_OBJECT):
        return f"{holder.id}.{called.attr}"
    if isinstance(holder, ast.Attribute) and holder.attr == _STORE_OBJECT:
        return f"{_STORE_OBJECT}.{called.attr}"
    return None


def _helper_called(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _disk_helpers(functions: list[ast.FunctionDef]) -> dict[str, str]:
    reaching: dict[str, str] = {}
    while True:
        found = {
            function.name: name
            for function in functions
            if function.name not in reaching
            for node in _in_body(function)
            if (name := _disk_name_called(node) or reaching.get(_helper_called(node) or ""))
        }
        if not found:
            return reaching
        reaching |= found


def _disk_calls(tree: ast.Module) -> Iterator[tuple[str, str]]:
    module_level = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    for function in ast.walk(tree):
        if not isinstance(function, ast.AsyncFunctionDef):
            continue
        defined = [node for node in ast.walk(function) if isinstance(node, ast.FunctionDef)]
        helpers = _disk_helpers(module_level + defined)
        for node in _in_body(function):
            helper = _helper_called(node)
            name = _disk_name_called(node)
            if name is not None:
                yield function.name, name
            if helper in helpers:
                yield function.name, f"{helper}, which calls {helpers[helper]}"


def test_a_coroutine_reaches_the_store_only_through_a_thread():
    breaking = [
        f"{module}.{coroutine}: {name}"
        for module, tree in _TREES.items()
        for coroutine, name in _disk_calls(tree)
    ]
    assert_equal(breaking, [], "disk calls on the event loop, not through asyncio.to_thread")


_OPENS_IN_THE_DEFAULT = ("squidpdf.core", "squidpdf.cli")


def _names_for(tree: ast.Module, name: str) -> set[str]:
    imported_as = {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == name and alias.asname
    }
    return {name} | imported_as


def _opens_without_a_driver(tree: ast.Module) -> Iterator[int]:
    names = _names_for(tree, "open_pdf")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = node.func
        by_name = isinstance(called, ast.Name) and called.id in names
        by_attribute = isinstance(called, ast.Attribute) and called.attr == "open_pdf"
        if not (by_name or by_attribute):
            continue
        given = {keyword.arg: keyword.value for keyword in node.keywords}
        factory = given.get("driver_factory")
        names_the_default = (
            isinstance(factory, ast.Name) and factory.id == "DEFAULT_DRIVER_FACTORY"
        )
        if factory is None or names_the_default:
            yield node.lineno


def test_every_feature_opens_a_pdf_in_the_driver_it_was_given():
    breaking = [
        f"{module}:{line}"
        for module, tree in _TREES.items()
        if not any(_within(module, default) for default in _OPENS_IN_THE_DEFAULT)
        for line in _opens_without_a_driver(tree)
    ]
    assert_equal(breaking, [], "opens of a PDF that name no driver_factory of their own")


def _names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        ("FOLDER_STORE", ("squidpdf.documents.store", "squidpdf.api.app")),
        ("FolderStore", ("squidpdf.documents.store",)),
        (
            "DEFAULT_DRIVER_FACTORY",
            ("squidpdf.core", "squidpdf.cli", "squidpdf.documents.store"),
        ),
    ],
    ids=[
        "only the app picks the default store; a feature uses the one it's handed",
        "only the store makes a folder store",
        "only core, the CLI and the default store name the default driver",
    ],
)
def test_only_the_edges_name_a_default(name, allowed):
    breaking = [
        module
        for module, tree in _TREES.items()
        if not any(_within(module, edge) for edge in allowed) and name in _names(tree)
    ]
    assert_equal(breaking, [], f"modules naming {name}")


_CORE_INSIDE = [name for name in _MODULES if name.startswith("squidpdf.core.")]


def _past_core_door(name: str) -> bool:
    return any(_within(name, inside) for inside in _CORE_INSIDE)


def test_outside_core_squidpdf_core_is_the_only_way_in():
    breaking = [
        module
        for module, imports in _MODULES.items()
        if not _within(module, "squidpdf.core")
        and any(_past_core_door(name) for name in imports)
    ]
    assert_equal(breaking, [], "modules outside core importing one of core's modules")


def test_nothing_inside_imports_the_package_itself():
    breaking = [module for module, imports in _MODULES.items() if "squidpdf" in imports]
    assert_equal(breaking, [], "modules importing the squidpdf package itself")


# `core.pdf.driver` is above `core.pdf`: text and fonts use it, and MuPDF's driver uses fonts.
_CORE_ORDER = (
    ("squidpdf.core.types", "squidpdf.core.constants"),
    ("squidpdf.core.app",),
    ("squidpdf.core.pdf.driver",),
    ("squidpdf.core.text",),
    ("squidpdf.core.fonts",),
    ("squidpdf.core.pdf",),
    ("squidpdf.core.plan",),
    ("squidpdf.core.writer", "squidpdf.core.fit", "squidpdf.core.redaction"),
    ("squidpdf.core.engine",),
    ("squidpdf.core",),
)
_CORE_ROWS = {package: row for row, packages in enumerate(_CORE_ORDER) for package in packages}


def _core_package(name: str) -> str:
    return max((package for package in _CORE_ROWS if _within(name, package)), key=len)


def test_each_core_package_imports_only_those_before_it_in_one_order():
    breaking = sorted(
        {
            f"{module} imports {_core_package(name)}"
            for module, imports in _MODULES.items()
            if _within(module, "squidpdf.core")
            for name in imports
            if _within(name, "squidpdf.core")
            and _CORE_ROWS[_core_package(name)] > _CORE_ROWS[_core_package(module)]
        }
    )
    assert_equal(breaking, [], "core modules importing a package that builds on theirs")


def test_every_core_module_is_in_a_package_the_order_lists():
    unlisted = [
        module
        for module in _MODULES
        if _within(module, "squidpdf.core")
        and module != "squidpdf.core"
        and _core_package(module) == "squidpdf.core"
    ]
    assert_equal(unlisted, [], "core modules in no package of _CORE_ORDER")


_EXCEPTIONS_BUILT_IN = {
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
}
_MAKERS = ("__init__", "__post_init__")


def _classes() -> Iterator[tuple[str, ast.ClassDef]]:
    for module, tree in _TREES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                yield module, node


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Attribute):
        return base.attr
    return base.id if isinstance(base, ast.Name) else ""


def _exceptions() -> set[str]:
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
    for method in node.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for name in _set_on_self(method):
                yield method.name, name


def _is_holder(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or _base_name(decorator.func) != "dataclass":
            continue
        options = {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords}
        return options.get("slots") is True and options.get("frozen") is not True
    return False


def test_no_class_holds_its_state_in_a_private_attribute():
    breaking = [
        f"{module}.{node.name}.{method}: self.{name}"
        for module, node in _classes()
        for method, name in _fields_set(node)
        if name.startswith("_")
    ]
    assert_equal(breaking, [], "private attributes set in src")


def test_only_an_exception_sets_its_fields_in_init():
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
    breaking = [
        f"{module}.{node.name}.{method}: self.{name}"
        for module, node in _classes()
        if not _is_holder(node)
        for method, name in _fields_set(node)
        if method not in _MAKERS
    ]
    assert_equal(breaking, [], "fields changed after an object is made, outside a holder")


# Tables kept out of a constants.py and out of their one reader on purpose, each with why.
_TABLES_ELSEWHERE = {
    "QUARTER_TURNS": "type data several modules read, beside its type, QuarterTurn",
    "CATALOG": "the faces we ship: data about files, not tuning, in core/fonts/catalog.py",
    "FACES": "the faces we ship, by name, beside CATALOG",
    "MUPDF_FAILURES": "the library's failures, beside its driver: a new library brings its own",
}


def _is_table(value: ast.expr) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp)):
        return True
    if not isinstance(value, (ast.Tuple, ast.List)):
        return False
    items = [item.value if isinstance(item, ast.Starred) else item for item in value.elts]
    return any(isinstance(item, (ast.Tuple, ast.Call)) for item in items)


def _assignments(tree: ast.Module) -> Iterator[tuple[ast.expr, ast.expr]]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            yield node.targets[0], node.value
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            yield node.target, node.value


def _tables() -> Iterator[tuple[str, str]]:
    for module, tree in _TREES.items():
        if module.endswith(".constants"):
            continue
        for target, value in _assignments(tree):
            if isinstance(target, ast.Name) and _is_table(value):
                yield module, target.id


def test_a_table_outside_a_constants_py_is_private_unless_named():
    tables = list(_tables())
    breaking = [
        f"{module}.{name}"
        for module, name in tables
        if not name.startswith("_") and name not in _TABLES_ELSEWHERE
    ]
    assert_equal(breaking, [], "public tables outside a constants.py")
    allowed_unused = set(_TABLES_ELSEWHERE) - {name for _module, name in tables}
    assert_equal(allowed_unused, set(), "tables let through by name that are no longer tables")


_NAME_ENDS = re.compile(r"[\[<>=~!; ]")


def _api_extra() -> list[str]:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["project"]["optional-dependencies"]["api"]
    return [_NAME_ENDS.split(requirement, maxsplit=1)[0] for requirement in requirements]


_WEB_FRAMEWORK = ["starlette", "pydantic"]

# Run in a fresh interpreter, so nothing the suite imported already hides a missing package.
# A module set to None in sys.modules raises ImportError when anything imports it.
_IMPORT_BLOCKED = """
import sys
sys.modules.update(dict.fromkeys(sys.argv[1:]))
import squidpdf.cli
"""


def test_the_cli_runs_without_the_api_extra():
    blocked = _api_extra() + _WEB_FRAMEWORK
    ran = subprocess.run(
        [sys.executable, "-c", _IMPORT_BLOCKED, *blocked],
        capture_output=True,
        text=True,
        check=False,
    )
    assert_equal(ran.stderr, "", f"importing the CLI with {', '.join(blocked)} blocked")
