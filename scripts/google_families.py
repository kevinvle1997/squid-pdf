"""Rebuild `fonts/google-families.json`: Google's font families at the pinned commit.

    uv run python scripts/google_families.py

Reads the commit from `GOOGLE_FONTS_COMMIT` in `core/constants.py`. Lists every
path and its git blob hash with a blobless, one-commit git fetch (no API, so
no rate limit), then fetches each family's METADATA.pb and licence from
raw.githubusercontent.com in parallel, checking each against its blob hash.
`--tree` and `--files` reuse a saved `git ls-tree -r` listing and files
already fetched (still checked), to rebuild without the network.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from squidpdf.core.constants import GOOGLE_FONTS_COMMIT
from squidpdf.core.google import blob_hash, raw_url

_REPO = "https://github.com/google/fonts.git"
_OUT = Path(__file__).parents[1] / "src" / "squidpdf" / "fonts" / "google-families.json"

_PER_FILE_TIMEOUT_S = 20  # a few KB each; far longer means something's wrong
_PARALLEL = 32  # requests at once: minutes one by one, seconds this way

# The folders families live in, one per licence. Only the OFL's text is read.
_LICENCE_FOLDERS = ("ofl", "apache", "ufl")
_OFL = "OFL.txt"

# Families Google renamed: the old name, as a document names the font, to the new family.
_ALIASES = {
    "sourcesanspro": "sourcesans3",
    "sourceserifpro": "sourceserif4",
}

# `fonts { ... }` blocks at the top level of METADATA.pb, and the fields read from each.
_FONT_BLOCK = re.compile(r"^fonts \{\n(.*?)^\}", re.MULTILINE | re.DOTALL)
_FIELD = re.compile(r'^\s*(\w+): "?([^"\n]*)"?$', re.MULTILINE)
_FAMILY_NAME = re.compile(r'^name: "([^"]+)"', re.MULTILINE)
# The OFL's own text says "Reserved Font Name" too; only the copyright lines above it count.
_OFL_BODY = "This Font Software is licensed"
_VARIABLE = 3  # where a file's entry says whether it's variable


def tree_listing(commit: str) -> str:
    """`git ls-tree -r` at `commit`, from a blobless fetch of that commit alone."""
    with tempfile.TemporaryDirectory() as scratch:
        git = ["git", "-C", scratch]
        subprocess.run([*git, "init", "-q"], check=True)
        fetch = ["fetch", "-q", "--depth", "1", "--filter=blob:none", _REPO, commit]
        subprocess.run([*git, *fetch], check=True)
        ls_tree = [*git, "ls-tree", "-r", commit]
        listed = subprocess.run(ls_tree, check=True, capture_output=True)
        return listed.stdout.decode()


def blobs_in(listing: str) -> dict[str, str]:
    """Each path in a `git ls-tree -r` listing, and its blob hash."""
    blobs: dict[str, str] = {}
    for line in listing.splitlines():
        about, path = line.split("\t", 1)
        _mode, kind, blob = about.split()
        if kind == "blob":
            blobs[path] = blob
    return blobs


def fetched(paths: list[str], *, blobs: dict[str, str], saved: Path | None) -> dict[str, bytes]:
    """Each path's bytes, from `saved` or fetched, every one checked against its blob hash."""

    def one(client: httpx.Client, path: str) -> bytes:
        """One file's bytes, from disk if it's there and right, else from GitHub."""
        on_disk = None if saved is None else saved / path
        kept = b"" if on_disk is None or not on_disk.exists() else on_disk.read_bytes()
        if blob_hash(kept) == blobs[path]:
            return kept
        response = client.get(raw_url(path))
        response.raise_for_status()
        if blob_hash(response.content) != blobs[path]:
            raise ValueError(f"{path}: not the file the commit has")
        return response.content

    with (
        httpx.Client(timeout=_PER_FILE_TIMEOUT_S) as client,
        ThreadPoolExecutor(_PARALLEL) as pool,
    ):
        contents = pool.map(lambda path: one(client, path), paths)
        return dict(zip(paths, contents, strict=True))


def fonts_in(metadata: str) -> list[dict[str, str]]:
    """Each font file METADATA.pb lists, as its fields."""
    return [dict(_FIELD.findall(block)) for block in _FONT_BLOCK.findall(metadata)]


def reserves_a_name(folder: str, contents: dict[str, bytes]) -> bool:
    """Whether a family's licence keeps its name from modified versions, such as a cut instance.

    The OFL says so in its copyright lines; the Ubuntu Font Licence asks every
    modified version to be renamed; Apache doesn't.
    """
    licence_folder = folder.split("/")[0]
    if licence_folder == "apache":
        return False
    licence = contents.get(f"{folder}/{_OFL}")  # .get: a UFL family, or an OFL one missing it
    if licence is None:
        return True  # no copyright lines to read: assume it reserves one
    header = licence.decode(errors="replace").split(_OFL_BODY, 1)[0]
    return "reserved font name" in header.lower()


def family_entry(folder: str, *, contents: dict[str, bytes], blobs: dict[str, str]) -> dict:
    """One family, as the engine reads it: only the files it would draw with.

    Its fixed-weight files if it has any, else its variable ones. Each file is
    `[name, weight, italic, variable, blob]`, a list to keep the JSON small.
    """
    metadata = contents[f"{folder}/METADATA.pb"].decode()
    files = [
        [
            font["filename"],
            int(font["weight"]),
            font["style"] == "italic",
            "[" in font["filename"],  # variable: its axes are in its name, "[wdth,wght]"
            blobs[f"{folder}/{font['filename']}"],
        ]
        for font in fonts_in(metadata)
        if f"{folder}/{font['filename']}" in blobs  # listed, but not in the commit: skip it
    ]
    static = [file for file in files if not file[_VARIABLE]]
    return {
        "folder": folder,
        "reserved_name": reserves_a_name(folder, contents),
        "files": static or files,
    }


def key_of(metadata: bytes) -> str:
    """A family's name as a document's font name gives it: no spaces, lowercased."""
    [name] = _FAMILY_NAME.findall(metadata.decode())
    return name.replace(" ", "").lower()


def main() -> int:
    """Rebuild the list and write it, one family per line so a new pin diffs by family."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tree", type=Path, help="a saved `git ls-tree -r` listing")
    parser.add_argument("--files", type=Path, help="a folder of files already fetched")
    args = parser.parse_args()

    listing = args.tree.read_text() if args.tree else tree_listing(GOOGLE_FONTS_COMMIT)
    blobs = blobs_in(listing)
    folders = sorted(
        path.rsplit("/", 1)[0]
        for path in blobs
        if path.split("/")[0] in _LICENCE_FOLDERS
        and path.count("/") == 2
        and path.endswith("/METADATA.pb")
    )
    every = [f"{folder}/{name}" for folder in folders for name in ("METADATA.pb", _OFL)]
    wanted = [path for path in every if path in blobs]
    print(f"{len(folders)} families, fetching {len(wanted)} files", file=sys.stderr)
    contents = fetched(wanted, blobs=blobs, saved=args.files)

    families: dict[str, dict] = {}
    for folder in folders:
        entry = family_entry(folder, contents=contents, blobs=blobs)
        key = key_of(contents[f"{folder}/METADATA.pb"])
        if entry["files"]:
            families.setdefault(key, entry)  # in folder order: a name used twice keeps one

    lines = [
        f"  {json.dumps(key)}: {json.dumps(entry, separators=(',', ':'))}"
        for key, entry in families.items()
    ]
    body = ",\n".join(lines)
    _OUT.write_text(
        "{\n"
        f'"commit": {json.dumps(GOOGLE_FONTS_COMMIT)},\n'
        f'"aliases": {json.dumps(_ALIASES)},\n'
        f'"families": {{\n{body}\n}}\n'
        "}\n"
    )
    print(f"{len(families)} families written to {_OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
