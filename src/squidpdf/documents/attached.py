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

# Each `<name's digest>.<digest>`: a name runs to 127 bytes, too long for a file's name in hex.
_ATTACHED = "fonts"
_ARRIVING = ".arriving-"
_ATTACHED_LOCK = ".fonts-lock"
_ATTACHED_DIGEST_SIZE = 8


@dataclass(frozen=True, slots=True)
class AttachedFiles:
    paths: dict[str, Path]

    @property
    def key(self) -> str:
        names = "/".join(sorted(path.name for path in self.paths.values()))
        return hashlib.blake2s(names.encode(), digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()

    def __getitem__(self, font_name: str) -> bytes:
        try:
            return self.paths[_name_digest_of(font_name)].read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(font_name) from exc

    def __contains__(self, font_name: str) -> bool:
        return _name_digest_of(font_name) in self.paths

    def can_keep(self, font_name: str) -> bool:
        return font_name in self or len(self.paths) < constants.MAX_FONTS


def attached_files(folder: Path) -> AttachedFiles:
    return AttachedFiles({_name_digest_in(path): path for path in _kept_copies(folder)})


def arriving_font(folder: Path) -> Path:
    attached = folder / _ATTACHED
    with full_disk_refused():
        try:
            attached.mkdir(exist_ok=True)
            handle, arriving = tempfile.mkstemp(dir=attached, prefix=_ARRIVING)
        except FileNotFoundError as exc:
            raise Gone from exc
    os.close(handle)
    return Path(arriving)


def keep_font(folder: Path, font_name: str, arrived: Path) -> None:
    digest = hashlib.blake2s(arrived.read_bytes(), digest_size=_ATTACHED_DIGEST_SIZE)
    kept = folder / _ATTACHED / f"{_name_digest_of(font_name)}.{digest.hexdigest()}"
    try:
        os.replace(arrived, kept)
    except FileNotFoundError as exc:
        raise Gone from exc
    drop_font(folder, font_name, keeping=kept)


def drop_font(folder: Path, font_name: str, *, keeping: Path | None = None) -> None:
    name_digest = _name_digest_of(font_name)
    # Every file, not one per font: mid-replacement, a font has two.
    for path in _kept_copies(folder):
        if _name_digest_in(path) == name_digest and path != keeping:
            path.unlink(missing_ok=True)


@contextmanager
def fonts_locked(folder: Path) -> Iterator[None]:
    try:
        lock = (folder / _ATTACHED_LOCK).open("a")
    except FileNotFoundError as exc:
        raise Gone from exc
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _kept_copies(folder: Path) -> list[Path]:
    try:
        listed = list((folder / _ATTACHED).iterdir())
    except FileNotFoundError:
        return []
    return [path for path in listed if not path.name.startswith(".")]


def _name_digest_of(font_name: str) -> str:
    # A name read from a file can hold a lone surrogate, which strict UTF-8 refuses.
    named = font_name.encode(errors="surrogatepass")
    return hashlib.blake2s(named, digest_size=_ATTACHED_DIGEST_SIZE).hexdigest()


def _name_digest_in(path: Path) -> str:
    name_digest, _digest = path.name.split(".")
    return name_digest
