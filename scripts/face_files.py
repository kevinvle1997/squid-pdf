"""Rebuild the browser's lists of the faces we ship: each one's file, and the letters it draws.

    uv run python scripts/face_files.py

The browser previews in the server's own files, by `face-files.json`: a file's name can't
be worked out from its face's. `face-letters.json` lists what each draws, and its fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

from squidpdf.core import CATALOG, Face, broadest, face_coverage

_FONTS = Path(__file__).parents[1] / "src" / "squidpdf" / "fonts"


def main() -> None:
    """Write both lists, each face by name, in the catalog's order."""
    files = {face.name: face.file for face in CATALOG}
    (_FONTS / "face-files.json").write_text(
        json.dumps(files, indent=2) + "\n", encoding="utf-8"
    )
    (_FONTS / "face-letters.json").write_text(face_letters_json(), encoding="utf-8")


def face_letters_json() -> str:
    """Every face's letters and broadest face, as `face-letters.json` holds them, one a line."""
    rows = [
        f"  {json.dumps(face.name)}: {json.dumps(_letters_of(face), separators=(',', ':'))}"
        for face in CATALOG
    ]
    return "{\n" + ",\n".join(rows) + "\n}\n"


def _letters_of(face: Face) -> dict[str, object]:
    """Where a line goes when `face` lacks a letter, and the code points it draws, as runs."""
    runs: list[list[int]] = []
    for ch in face_coverage(face).drawable():
        code = ord(ch)
        # One past the run before it: the run grows.
        if runs and runs[-1][1] == code - 1:
            runs[-1][1] = code
        else:
            runs.append([code, code])
    letters = [first if first == last else [first, last] for first, last in runs]
    return {"broadest": broadest(face).name, "letters": letters}


if __name__ == "__main__":
    main()
