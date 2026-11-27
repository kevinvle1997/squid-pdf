from __future__ import annotations

import pytest

from squidpdf.documents import store
from squidpdf.documents.store import _KeptIndexes


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "data"))


@pytest.fixture
def kept_indexes() -> _KeptIndexes:
    """By their private name: no public call forgets them."""
    return store._kept_indexes
