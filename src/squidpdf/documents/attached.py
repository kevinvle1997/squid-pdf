"""The user's own copies of a document's fonts, kept in its folder: listed, kept, removed.

Each copy's file is named for its font's name, so a new one replaces the old, and one
still arriving is named apart until it's checked.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from squidpdf.documents import constants
from squidpdf.documents.disk import full_disk_refused
from squidpdf.documents.errors import Gone

# The user's own copies of a document's fonts, in its folder, each `<name's digest>.<digest>`:
# a name runs to 127 bytes, too long for a file's name in hex.
_ATTACHED = "fonts"
_ARRIVING = ".arriving-"  # a copy still coming in, or being checked: not attached yet
_ATTACHED_LOCK = ".fonts-lock"  # held by whoever attaches or removes a copy, and analyses
_ATTACHED_DIGEST_SIZE = 8  # bytes of each digest in a copy's file name, and of a set's


@dataclass(frozen=True, slots=True, eq=False)
class AttachedFiles:
    """The user's own copies of a document's fonts, by the font's name: each read when asked.

    A render needs only the copies its fonts lend from, so none is read before then.
    """

    paths: dict[str, Path]  # each copy's file, by the digest of its font's name

    @property
    def key(self) -> str:
        """What tells this set of copies from another, for the analysis's file names."""
        names = "/".join(sorted(path.name for path in self.paths.values()))
        return hashlib.blake2s(names.encode(), digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()

    def __getitem__(self, font_name: str) -> bytes:
        """The copy for `font_name`. Raises KeyError if it was removed since it was listed."""
        try:
            return self.paths[_name_digest_of(font_name)].read_bytes()
        except FileNotFoundError as exc:  # removed or replaced since: no copy, not no document
            raise KeyError(font_name) from exc

    def __contains__(self, font_name: str) -> bool:
        """Whether there's a copy for `font_name`, read without reading it."""
        return _name_digest_of(font_name) in self.paths

    def can_keep(self, font_name: str) -> bool:
        """Whether a copy of `font_name` can be kept: as a replacement, or under `MAX_FONTS`."""
        return font_name in self or len(self.paths) < constants.MAX_FONTS


def attached_files(folder: Path) -> AttachedFiles:
    """The user's own copies of the document's fonts, listed now, each read when asked for."""
    return AttachedFiles({_name_digest_in(path): path for path in _kept_copies(folder)})


def arriving_font(folder: Path) -> Path:
    """A new, empty file for a copy to come into, in the document's folder: it goes with it."""
    attached = folder / _ATTACHED
    with full_disk_refused():
        try:
            attached.mkdir(exist_ok=True)
            handle, arriving = tempfile.mkstemp(dir=attached, prefix=_ARRIVING)
        except FileNotFoundError as exc:  # the document was deleted since it was found
            raise Gone from exc
    os.close(handle)
    return Path(arriving)


def keep_font(folder: Path, font_name: str, arrived: Path) -> None:
    """Attach the copy that `arrived` for `font_name`, in place of any it had."""
    digest = hashlib.blake2s(arrived.read_bytes(), digest_size=_ATTACHED_DIGEST_SIZE)
    kept = folder / _ATTACHED / f"{_name_digest_of(font_name)}.{digest.hexdigest()}"
    try:
        os.replace(arrived, kept)
    except FileNotFoundError as exc:  # the document was deleted mid-way
        raise Gone from exc
    drop_font(folder, font_name, keeping=kept)


def drop_font(folder: Path, font_name: str, *, keeping: Path | None = None) -> None:
    """Remove the user's copy of `font_name`, all but `keeping`; nothing if there's none."""
    name_digest = _name_digest_of(font_name)
    # Every file, not one per font: mid-replacement, a font has two.
    for path in _kept_copies(folder):
        if _name_digest_in(path) == name_digest and path != keeping:
            path.unlink(missing_ok=True)


@contextmanager
def fonts_locked(folder: Path) -> Iterator[None]:
    """Hold the document's copies still: one attach or removal at a time, across workers."""
    try:
        lock = (folder / _ATTACHED_LOCK).open("a")
    except FileNotFoundError as exc:  # the document was deleted since it was found
        raise Gone from exc
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released as the file closes
        yield


def _kept_copies(folder: Path) -> list[Path]:
    """Every copy kept in the document's folder, as its files lie."""
    try:
        listed = list((folder / _ATTACHED).iterdir())
    except FileNotFoundError:  # none was ever attached, or the document was deleted
        return []
    # A dot first: one still arriving, not attached yet.
    return [path for path in listed if not path.name.startswith(".")]


def _name_digest_of(font_name: str) -> str:
    """What a copy's file is named for: its font's name, at a length any name has."""
    # A name read from a file can hold a lone surrogate, which strict UTF-8 refuses.
    named = font_name.encode(errors="surrogatepass")
    return hashlib.blake2s(named, digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()


def _name_digest_in(path: Path) -> str:
    """The digest of the name of the font a kept copy is for, from its file's name."""
    name_digest, _digest = path.name.split(".")
    return name_digest
