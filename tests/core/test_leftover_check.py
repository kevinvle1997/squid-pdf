"""The saved-file check finds any word of a removed span still in its box (Rule 4)."""

from __future__ import annotations

import pymupdf

from squidpdf.core import new_text, open_pdf
from tests.helpers import assert_equal

_CARD = "Card 4111 2222 3333"  # four groups: the check once passed with three of them left
_LABELLED = "Card A 4111"  # "A" is a word of one letter, as in a name
_LABEL = "[REDACTED]"  # what people draw over a redaction; it has an A in it


def test_a_removal_that_left_some_words_in_the_box_is_still_there(tmp_path):
    """It flagged a span only when its whole text was left, and passed three groups of four."""
    path, leaky = str(tmp_path / "card.pdf"), str(tmp_path / "leaky.pdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 96), _CARD, fontname="helv", fontsize=12)
    doc.save(path)
    with open_pdf(path) as engine:
        [span] = list(engine.index())
    # An erase that missed three groups of four, made by hand: only "Card" goes.
    doc = pymupdf.open(path)
    [card] = doc[0].search_for("Card")
    doc[0].add_redact_annot(card)
    doc[0].apply_redactions()
    doc.save(leaky)

    with open_pdf(leaky) as saved:
        left = saved.still_there([span])

    assert_equal(left, [span], "spans with some of their text still in their box")


def test_a_label_drawn_over_a_removal_is_not_its_text_left_behind(tmp_path):
    """Every word once counted alone, so the "A" of "Card A 4111" was found in "[REDACTED]"."""
    path, saved_path = str(tmp_path / "card.pdf"), str(tmp_path / "labelled.pdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 96), _LABELLED, fontname="helv", fontsize=12)
    doc.save(path)
    with open_pdf(path) as engine:
        [span] = list(engine.index())
        engine.remove([span])
        label = new_text(0, origin=span.origin, text=_LABEL, size=12, font="Carlito Regular")
        engine.draw(label, _LABEL)
        engine.save(saved_path)

    with open_pdf(saved_path) as saved:
        left = saved.still_there([span])

    assert_equal(left, [], "spans with some of their text still in their box")
