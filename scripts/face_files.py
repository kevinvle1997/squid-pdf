"""Rebuild `fonts/face-files.json`: each face we ship, by name, and its file, for the browser.

    uv run python scripts/face_files.py

The browser previews in the very files the server draws with, and finds each one
here: a family filed as its source ships it (FreeSans.ttf, lmroman10-regular.otf)
can't be worked out from the face's name.
"""

from __future__ import annotations

import json
from pathlib import Path

from squidpdf.core import CATALOG

_OUT = Path(__file__).parents[1] / "src" / "squidpdf" / "fonts" / "face-files.json"


def main() -> None:
    """Write every face's file, by name, in the catalog's order."""
    files = {face.name: face.file for face in CATALOG}
    _OUT.write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
