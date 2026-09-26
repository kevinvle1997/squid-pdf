"""The import rules the docstrings state, checked: which package may import which.

Read from the source, so a rule holds however deep an import hides, even one
inside a function. test_imports.py checks what's actually loaded at run time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.helpers import assert_equal

_SRC = Path(__file__).parents[1] / "src"


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
    """`src/squidpdf/core/pdf.py` -> `squidpdf.core.pdf`."""
    parts = path.relative_to(_SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


_MODULES = {_module(path): _imports(path) for path in sorted(_SRC.rglob("*.py"))}


def _within(name: str, package: str) -> bool:
    """Whether module `name` is `package` or inside it."""
    return name == package or name.startswith(package + ".")


@pytest.mark.parametrize(
    ("importers", "imported", "allowed"),
    [
        ("squidpdf", "pymupdf", ["squidpdf.core.pdf", "squidpdf.core.mupdf"]),
        ("squidpdf", "squidpdf.core.mupdf", ["squidpdf.core"]),
        ("squidpdf", "squidpdf.core.pdf", ["squidpdf.core"]),
        ("squidpdf.core", "squidpdf.editing", []),
        ("squidpdf.core", "squidpdf.documents", []),
        ("squidpdf.core", "squidpdf.api", []),
        ("squidpdf.core", "squidpdf.cli", []),
        ("squidpdf.documents", "squidpdf.editing", []),
        (
            "squidpdf",
            "squidpdf.api",
            ["squidpdf.api", "squidpdf.documents.api", "squidpdf.editing.api"],
        ),
    ],
    ids=[
        "only core/pdf.py and core/mupdf.py talk to MuPDF",
        "outside core, nothing names the backend: open_pdf is the way in",
        "outside core, nothing reads MuPDF's low-level wrapper",
        "core imports no feature: editing",
        "core imports no feature: documents",
        "core imports no feature: the web layer",
        "core imports no feature: the CLI",
        "editing builds on documents, never the other way",
        "the web layer stays in api/ and each feature's api.py, so the CLI runs without it",
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
