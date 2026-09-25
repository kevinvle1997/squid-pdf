"""Every document these tests make goes under a fresh folder."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUIDPDF_DATA", str(tmp_path / "data"))
