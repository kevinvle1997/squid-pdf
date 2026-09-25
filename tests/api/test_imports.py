"""The `api` extra is optional, so the CLI must never pull in the web framework."""

from __future__ import annotations

import subprocess
import sys

from tests.helpers import assert_equal

_FRAMEWORK = ("fastapi", "starlette", "pydantic")


def test_importing_the_cli_loads_no_web_framework():
    # A fresh interpreter: this one has already imported FastAPI for other tests.
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys, squidpdf.cli; print([m for m in {_FRAMEWORK!r} if m in sys.modules])",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert_equal(loaded, "[]", "web framework modules loaded by importing the CLI")
