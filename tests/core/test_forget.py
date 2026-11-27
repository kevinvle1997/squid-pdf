"""Each test fills every field, so a field added later and missed by a forget fails here."""

from __future__ import annotations

import threading

from squidpdf.core.fonts.document import _FontCache  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.core.fonts.google import _RetryRecord  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.core.fonts.pool import _KeptWidths  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from squidpdf.core.writer import _PageNames  # noqa: PLC2701 (a holder's forget test needs a fresh one)
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_every_field_filled


def test_keeping_pages_forgets_the_fonts_read_by_page_number(engine):
    index = engine.index()
    engine.assess(index)
    for span in index:
        engine.widths(span)
    cache = engine.fonts.cache
    assert_every_field_filled(cache, _FontCache(), "the cache once every span is judged")

    engine.keep_pages([EMBEDDED_PAGE, REFERENCED_PAGE])

    assert_equal(cache, _FontCache(), "the cache once the pages are renumbered")


def test_keeping_pages_forgets_each_pages_resource_names(engine):
    on_page = {span.page: span for span in engine.index()}
    for page in (REFERENCED_PAGE, EMBEDDED_PAGE):
        engine.draw(on_page[page], "Invoices")
    names = engine.writer.names
    assert_every_field_filled(names, _PageNames(), "the names once both pages are drawn on")

    engine.keep_pages([EMBEDDED_PAGE, REFERENCED_PAGE])

    assert_equal(names, _PageNames(), "the names once the pages are renumbered")


def test_a_retry_record_that_forgets_equals_a_fresh_one():
    retries = _RetryRecord()
    retries.hold("ofl/poppins/Poppins-Regular.ttf", until=5.0, answered=threading.Event())
    assert_every_field_filled(retries, _RetryRecord(), "the record once a fetch got no answer")

    retries.forget()

    assert_equal(retries, _RetryRecord(), "the record once it forgot")


def test_kept_widths_that_forget_equal_fresh_ones():
    kept = _KeptWidths()
    kept.widths(b"a digest", lambda: {"A": 600.0})
    assert_every_field_filled(kept, _KeptWidths(), "the widths once one copy is measured")

    kept.forget()

    assert_equal(kept, _KeptWidths(), "the widths once they forgot")
