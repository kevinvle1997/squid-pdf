"""A part that forgets equals a fresh one afterwards, so nothing it read before is kept.

Rebuilding a part was fresh by construction; forgetting is only as fresh as its
`forget_` method. Each test fills every field the way the engine does, forgets,
then compares with a new one, so a field added later and missed fails here. The
engine's parts forget through `keep_pages`, the one place that renumbers pages.
"""

from __future__ import annotations

from squidpdf.core.fonts.document import FontCache
from squidpdf.core.writer import PageNames
from tests.conftest import EMBEDDED_PAGE, REFERENCED_PAGE
from tests.helpers import assert_equal, assert_every_field_filled


def test_keeping_pages_forgets_the_fonts_read_by_page_number(engine):
    """`keep_pages` renumbers the pages: nothing read through the old numbers may stay."""
    engine.assess(engine.index())
    cache = engine.fonts.cache
    assert_every_field_filled(cache, FontCache(), "the cache once every span is judged")

    engine.keep_pages([EMBEDDED_PAGE, REFERENCED_PAGE])

    assert_equal(cache, FontCache(), "the cache once the pages are renumbered")


def test_keeping_pages_forgets_each_pages_resource_names(engine):
    """A page's resource names are by page number, which `keep_pages` changes."""
    on_page = {span.page: span for span in engine.index()}
    # A face we ship draws on the page that only names its fonts; the file's own, on the other.
    for page in (REFERENCED_PAGE, EMBEDDED_PAGE):
        engine.draw(on_page[page], "Invoices")
    names = engine.writer.names
    assert_every_field_filled(names, PageNames(), "the names once both pages are drawn on")

    engine.keep_pages([EMBEDDED_PAGE, REFERENCED_PAGE])

    assert_equal(names, PageNames(), "the names once the pages are renumbered")
