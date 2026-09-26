"""An edit refuses what nothing could draw, however it's made."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from squidpdf.editing import Insert

_SIGNATURE = Insert(0, (72.0, 700.0), "Signed", 12.0)


@pytest.mark.parametrize(
    "change",
    [
        {"size": 0.0},
        {"size": -12.0},
        {"size": math.inf},
        {"size": math.nan},
        {"color": (1.5, 0.0, 0.0)},
        {"color": (math.nan, 0.0, 0.0)},
        {"origin": (math.nan, 700.0)},
        {"origin": (72.0, math.inf)},
    ],
    ids=[
        "size 0",
        "a size below 0",
        "an infinite size",
        "a size that is NaN",
        "a color past 1",
        "a color that is NaN",
        "an origin that is NaN",
        "an infinite origin",
    ],
)
def test_an_insert_nothing_can_draw_is_refused_when_made(change):
    with pytest.raises(ValueError):
        replace(_SIGNATURE, **change)
