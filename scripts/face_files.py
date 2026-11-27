"""Rebuild the browser's lists of our faces: a file's name can't be told from its face's."""

from __future__ import annotations

import json
from pathlib import Path

from squidpdf.core import CATALOG, Face, broadest, face_coverage

_FONTS = Path(__file__).parents[1] / "src" / "squidpdf" / "fonts"


def main() -> None:
    files = {face.name: face.file for face in CATALOG}
    (_FONTS / "face-files.json").write_text(
        json.dumps(files, indent=2) + "\n", encoding="utf-8"
    )
    (_FONTS / "face-letters.json").write_text(_face_letters_json(), encoding="utf-8")


def _face_letters_json() -> str:
    rows = [
        f"  {json.dumps(face.name)}: {json.dumps(_letters_of(face), separators=(',', ':'))}"
        for face in CATALOG
    ]
    return "{\n" + ",\n".join(rows) + "\n}\n"


def _letters_of(face: Face) -> dict[str, object]:
    runs: list[list[int]] = []
    for ch in face_coverage(face).drawable():
        code = ord(ch)
        if runs and runs[-1][1] == code - 1:
            runs[-1][1] = code
        else:
            runs.append([code, code])
    letters = [first if first == last else [first, last] for first, last in runs]
    return {"broadest": broadest(face).name, "letters": letters}


if __name__ == "__main__":
    main()
