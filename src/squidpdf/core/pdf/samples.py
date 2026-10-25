"""Sample PDFs made from nothing: the CLI's `sample` and the long fixture's pages.

Written with PyMuPDF's everyday calls, so they live in `core/pdf/` beside the driver.
"""

from __future__ import annotations

import random

import pymupdf

# The long fixture's page: a contract's body text, set the way a word processor sets it.
_DENSE_SIZE = 10.5  # points
_DENSE_LEADING = 14.0  # points from one baseline to the next
_DENSE_TOP = 108.0  # the first body line's baseline
_DENSE_BOTTOM = 770.0  # no baseline below this
_DENSE_WORDS = (
    "the provider shall deliver services under this agreement within the term and "
    "notify the client in writing of any change to the schedule fees or invoices "
    "each party keeps confidential information secret and uses it only for the "
    "purpose agreed liability is limited to the fees paid in the period before the claim"
).split()
_DENSE_TERMS = ("the Services", "the Client", "the Provider", "Confidential Information")
# The sample's opening line; the long fixture's first page opens with it, for a test to find.
_OPENING = "This agreement is made on 14 March 2026 between"


def write_sample(path: str) -> None:
    """A two-page sample: one page whose fonts are only named, one where one is stored."""
    doc = pymupdf.open()
    # "tibo" and "tiro" are MuPDF's short names for Times Bold and Times Roman,
    # which it never puts in the file.
    referenced = doc.new_page()
    referenced.insert_text((72, 96), "SERVICES AGREEMENT", fontname="tibo", fontsize=13)
    referenced.insert_text(
        (72, 128),
        _OPENING,
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 146),
        "Wescott Analytics Ltd and Lindqvist & Rowe LLP.",
        fontname="tiro",
        fontsize=11,
    )
    referenced.insert_text(
        (72, 176),
        "The Client shall pay 48,500 per quarter in arrears.",
        fontname="tiro",
        fontsize=11,
    )

    # Embedded then subsetted, the way a real generator leaves it, so only the
    # glyphs this page used survive and typing an accent will fail.
    embedded = doc.new_page()
    # "emb" is only the name the page files the font under.
    embedded.insert_font(fontname="emb", fontbuffer=pymupdf.Font("tiro").buffer)
    embedded.insert_text((72, 96), "Schedule 1 - Scope of work", fontname="emb", fontsize=12)
    embedded.insert_text(
        (72, 124),
        "Delivery begins 14 March 2026 and runs eighteen months.",
        fontname="emb",
        fontsize=11,
    )
    doc.subset_fonts(verbose=False)

    doc.save(path)
    doc.close()


def write_dense(path: str, *, pages: int) -> None:
    """A long contract of made-up clauses, for timing the browser on full pages.

    Every line is a span, and one in three has a defined term set in bold, as a
    contract's are, so a page carries as many spans as a real one. The words are
    the same on every run. Page 1 opens with the sample's line, for a test to find.
    """
    choose = random.Random(0)
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((72, 84), f"Schedule {number + 1}", fontname="tibo", fontsize=13)
        baseline = _DENSE_TOP
        if number == 0:
            page.insert_text((72, baseline), _OPENING, fontname="tiro", fontsize=_DENSE_SIZE)
            baseline += _DENSE_LEADING
        while baseline <= _DENSE_BOTTOM:
            x = 72.0
            for text, font in _dense_runs(choose):
                page.insert_text((x, baseline), text, fontname=font, fontsize=_DENSE_SIZE)
                x += pymupdf.get_text_length(text, fontname=font, fontsize=_DENSE_SIZE)
            baseline += _DENSE_LEADING
    doc.save(path)
    doc.close()


def _dense_runs(choose: random.Random) -> list[tuple[str, str]]:
    """One line of made-up words, as its runs and their fonts; one in three has a bold term."""
    line = [choose.choice(_DENSE_WORDS) for _ in range(choose.randint(9, 12))]
    # Plain: the whole line in the body font.
    if choose.random() >= 1 / 3:
        return [(" ".join(line) + " ", "tiro")]
    cut = choose.randint(2, len(line) - 2)
    return [
        (" ".join(line[:cut]) + " ", "tiro"),
        (choose.choice(_DENSE_TERMS) + " ", "tibo"),
        (" ".join(line[cut:]), "tiro"),
    ]
