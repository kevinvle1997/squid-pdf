"""Other copies of a font lend letters only when a line needs them."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import pymupdf

from squidpdf.core import FontSources, Message, open_pdf
from squidpdf.core.fonts import pool
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.core.fonts.google import GoogleFile
from squidpdf.core.fonts.pool import _KeptWidths  # noqa: PLC2701 (the bound on a holder kept for a worker's life)
from squidpdf.core.pdf.driver import PdfDriver
from squidpdf.core.pdf.mupdf import open_driver
from squidpdf.core.types import PageFont
from tests.conftest import POPPINS
from tests.helpers import assert_equal, assert_in

_SIZE = 14.0
_COPIES = 100  # copies from outside measured in one worker: far past how many it keeps

# One trimmed copy of Times per page. Page 0's lacks Y, a and y; page 1's has all
# three; page 2's alone has J. Each shares enough letters with page 0's to be vouched for.
_TEXTS = ("Hello there", "Yearly quiz", "Hello Jim, three")


def _copy_per_page(path: str, texts: tuple[str, ...] = _TEXTS) -> str:
    """One-page PDFs, one per text, each with its own trimmed copy of Times, joined into one.

    As merging documents leaves them: each page keeps the copy it came with,
    trimmed (subset) to its own letters, under one name with its own prefix.
    """
    joined = pymupdf.open()
    for text in texts:
        single = pymupdf.open()
        page = single.new_page()
        # "own" is only the name the page files the font under.
        page.insert_font(fontname="own", fontbuffer=pymupdf.Font("tiro").buffer)
        page.insert_text((72, 96), text, fontname="own", fontsize=_SIZE)
        single.subset_fonts(verbose=False)
        # Saved and reopened, as separate files would be, before joining.
        joined.insert_pdf(pymupdf.open("pdf", single.tobytes()))
    joined.save(path)
    return path


@dataclass(frozen=True, slots=True, eq=False)
class _NotingDriver:
    """MuPDF's driver, noting each font object the engine reads out and each page it lists."""

    driver: PdfDriver
    read_out: list[int]
    listed: list[int]

    def font_bytes(self, xref: int) -> bytes:
        """The real driver's answer, noted."""
        self.read_out.append(xref)
        return self.driver.font_bytes(xref)

    def fonts(self, page: int) -> list[PageFont]:
        """The real driver's answer, noted."""
        self.listed.append(page)
        return self.driver.fonts(page)

    def __getattr__(self, name: str) -> Any:
        """Every other call, as the real driver answers it."""
        return getattr(self.driver, name)


def _noting_driver(path: str, *, read_out: list[int], listed: list[int]) -> PdfDriver:
    """A driver factory for `open_pdf`: MuPDF's driver, noting into `read_out` and `listed`."""
    noting = _NotingDriver(open_driver(path), read_out=read_out, listed=listed)
    # A PdfDriver through __getattr__, which mypy reads and ty doesn't yet.
    return cast(PdfDriver, noting)


def _copies(path: str) -> list[int]:
    """Each page's copy of the font, by its object number, page by page."""
    doc = pymupdf.open(path)
    return [xref for number in range(doc.page_count) for xref, *_ in doc[number].get_fonts()]


def test_a_copy_a_line_doesnt_need_is_never_opened(tmp_path):
    path = _copy_per_page(str(tmp_path / "copies.pdf"))
    first, second, third = _copies(path)
    read_out: list[int] = []
    listed: list[int] = []
    noting = partial(_noting_driver, read_out=read_out, listed=listed)

    with open_pdf(path, driver_factory=noting) as engine:
        span = next(span for span in engine.index() if span.page == 0)
        read_out.clear()
        listed.clear()
        # Page 0's own copy draws every letter: no other page or copy is looked at.
        own_only = engine.fit.plan_for(span, "Hello other").missing
        after_own = (list(read_out), list(listed))
        # Y, a and y come from page 1's copy, the nearest that has them: page 2's stays shut.
        borrowed = engine.fit.plan_for(span, "Yearly Hello").missing
        after_borrowing = (list(read_out), list(listed))
        # The browser's list of letters is the whole pool, page 2's J with it.
        widths = engine.widths(span)

    assert_equal(own_only, [], "letters page 0's copy lacks for its own words")
    looked_at = "copies read out, and pages listed"
    assert_equal(after_own, ([first], [0]), looked_at)
    assert_equal(borrowed, [], "letters no copy draws")
    assert_equal(after_borrowing, ([first, second], [0, 1]), looked_at)
    assert_in("J", widths, "letters the browser is told the pool draws")
    assert_in(third, read_out, "copies read out once the whole pool is asked for")


# Page 1's copy draws the Y, a and y page 0's lacks; pages 2 to 7 repeat page 0's words.
_REPEATED = ("Hello there", "Yearly quiz", *["Hello there"] * 6)


def test_a_pool_takes_in_no_copy_once_it_has_every_letter_the_files_copies_draw(
    tmp_path, monkeypatch
):
    """Once a pool has seen every copy of a merged file's font, the next stops early."""
    path = _copy_per_page(str(tmp_path / "copies.pdf"), _REPEATED)
    checked: list[int] = []
    why_turned_away = pool.why_turned_away

    def counted(
        other: pool.FontCopy, *, own: pool.FontCopy, letters: Mapping[str, pool.FontCopy]
    ) -> Message | None:
        checked.append(other.font.xref)
        return why_turned_away(other, own=own, letters=letters)

    monkeypatch.setattr(pool, "why_turned_away", counted)
    with open_pdf(path) as engine:
        on_page = {span.page: span for span in engine.index()}
        # Page 0's whole pool: every other copy is taken in, so all their letters are known.
        engine.widths(on_page[0])
        checked.clear()
        widths = engine.widths(on_page[1])
        taken_in = len(checked)
    with open_pdf(path) as engine:
        walked_all = engine.widths(next(span for span in engine.index() if span.page == 1))

    # Page 0's copy, the nearest, brings every letter the rest draw: none of them is opened.
    assert_equal(taken_in, 1, "copies page 1's pool takes in once every copy is known")
    assert_equal(widths, walked_all, "page 1's letters, against a pool that took in every copy")


def _missing_with_google(path: str, font_file: bytes) -> list[str]:
    """What the first span can't draw of "Yearly Hello", with `font_file` as Google's copy."""

    def fetch(_file: GoogleFile) -> bytes:
        return font_file

    with open_pdf(path, sources=FontSources(google=fetch)) as engine:
        span = next(iter(engine.index()))
        return engine.fit.plan_for(span, "Yearly Hello").missing


def test_googles_copy_is_measured_by_its_bytes_not_its_name(poppins_subset):
    """A process keeps what Google's copies draw, and must not answer for other bytes."""
    missing_with_google = _missing_with_google(poppins_subset, POPPINS.read_bytes())
    # Liberation Sans handed back under Poppins' name: other widths, so it lends nothing.
    missing_with_impostor = _missing_with_google(
        poppins_subset, face_bytes(FACES["Liberation Sans Regular"])
    )

    assert_equal(missing_with_google, [], "letters Google's Poppins leaves missing")
    assert_equal(
        missing_with_impostor, ["Y", "a", "y"], "letters another font by its name leaves"
    )


def test_the_users_copy_lends_before_googles_so_google_is_never_asked(poppins_subset):
    """It's likely the very release the document used, and it saves a fetch."""
    asked: list[GoogleFile] = []

    def fetch(file: GoogleFile) -> bytes:
        asked.append(file)
        return POPPINS.read_bytes()

    sources = FontSources(google=fetch, attached={"Poppins-Regular": POPPINS.read_bytes()})
    with open_pdf(poppins_subset, sources=sources) as engine:
        span = next(iter(engine.index()))
        missing = engine.fit.plan_for(span, "Yearly Hello").missing

    assert_equal(missing, [], "letters no copy draws")
    assert_equal(asked, [], "files asked of Google")


def test_a_worker_keeps_the_widths_of_only_its_latest_copies_from_outside():
    """A long-lived worker stays small: past its bound, the copy measured longest ago goes."""
    kept = _KeptWidths()
    measured: list[bytes] = []

    def measure(digest: bytes) -> dict[str, float]:
        measured.append(digest)
        return {"A": 600.0}

    digests = [n.to_bytes(2) for n in range(_COPIES)]
    for digest in digests:
        kept.widths(digest, partial(measure, digest))
    measured.clear()
    newest, oldest = digests[-1], digests[0]
    for digest in (newest, oldest):
        kept.widths(digest, partial(measure, digest))

    assert_equal(measured, [oldest], "copies measured again: the oldest, not the newest")
