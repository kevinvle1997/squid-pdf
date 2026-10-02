"""The import rules the docstrings state, checked: which package may import which.

Read from the source, so a rule holds however deep an import hides, even one
inside a function.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.helpers import assert_equal

_ROOT = Path(__file__).parents[1]
_SRC = _ROOT / "src"


def _imports(path: Path) -> set[str]:
    """Every module a file imports, counting `P.N` for each `from P import N`."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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


_MODULES = {_module(path): _imports(path) for path in sorted(_SRC.rglob("*.py"))}

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
