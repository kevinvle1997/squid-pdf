"""What the driver promises the engine, whatever library is underneath."""

from __future__ import annotations

import pytest

from squidpdf.core import Message
from squidpdf.core.driver import DriverError
from squidpdf.core.mupdf import MuPDFDriver
from tests.helpers import assert_equal


def test_empty_bytes_are_not_a_font_the_driver_opens(pdf):
    """MuPDF opened Noto Serif for them, so an empty cache file measured as a real font."""
    driver = MuPDFDriver(pdf)
    try:
        with pytest.raises(DriverError) as raised:
            driver.open_font(b"")
    finally:
        driver.close()

    assert_equal(raised.value.reason, Message("font_unreadable"), "why it can't be used")
