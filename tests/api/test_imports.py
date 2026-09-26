"""The web framework stays in `api/` and the `api.py` files.

The `api` extra is optional, so the CLI must run without it; and pool work
must import without it, so the worker only loads what the PDF needs.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.helpers import assert_equal

_FRAMEWORK = ("fastapi", "starlette", "pydantic")


@pytest.mark.parametrize(
    "module",
    [
        "squidpdf.cli",
        "squidpdf.documents.analyse",
        "squidpdf.editing.work",
        "squidpdf.editing.fonts",
    ],
)
def test_importing_it_loads_no_web_framework(module):
    # A fresh interpreter: this one has already imported FastAPI for other tests.
    code = f"import sys, {module}; print([m for m in {_FRAMEWORK!r} if m in sys.modules])"
    run = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert_equal(
        run.stdout.strip(), "[]", f"web framework modules loaded by importing {module}"
    )
