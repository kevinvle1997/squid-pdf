"""Every document these tests make goes under a fresh folder."""

from __future__ import annotations

import pytest

from squidpdf.documents import store
from squidpdf.documents.store import _KeptIndexes


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    """Point the store at a folder of this test's own."""
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "data"))


@pytest.fixture
def kept_indexes() -> _KeptIndexes:
    """This worker's kept indexes, by their private name: no public call forgets them.

    Forgotten, the next read is a worker's that saved none: from its file.
    """
    return store._kept_indexes
