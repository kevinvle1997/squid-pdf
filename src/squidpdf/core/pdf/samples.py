from __future__ import annotations

import random

import pymupdf

_DENSE_SIZE = 10.5
_DENSE_LEADING = 14.0
_DENSE_TOP = 108.0
_DENSE_BOTTOM = 770.0
_DENSE_WORDS = (
    "the provider shall deliver services under this agreement within the term and "
    "notify the client in writing of any change to the schedule fees or invoices "
    "each party keeps confidential information secret and uses it only for the "
    "purpose agreed liability is limited to the fees paid in the period before the claim"
).split()
_DENSE_TERMS = ("the Services", "the Client", "the Provider", "Confidential Information")
_OPENING = "This agreement is made on 14 March 2026 between"


def write_sample(path: str) -> None:
    doc = pymupdf.open()
    # MuPDF's short names for Times Bold and Roman, which it never puts in the file.
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

    # Subsetted, as a real generator leaves it, so typing an accent will fail.
    embedded = doc.new_page()
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
    line = [choose.choice(_DENSE_WORDS) for _ in range(choose.randint(9, 12))]
    if choose.random() >= 1 / 3:
        return [(" ".join(line) + " ", "tiro")]
    cut = choose.randint(2, len(line) - 2)
    return [
        (" ".join(line[:cut]) + " ", "tiro"),
        (choose.choice(_DENSE_TERMS) + " ", "tibo"),
        (" ".join(line[cut:]), "tiro"),
    ]
