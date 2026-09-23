"""One pass through the public API, top to bottom.

Everything under tests/core and tests/editing exercises one piece in isolation.
This is the one place that checks they still fit together the way
squidpdf/__init__.py promises they do — import from `squidpdf`, not a submodule.
"""

from __future__ import annotations

import pymupdf

import squidpdf
from squidpdf.editing import apply, verify_redactions
from tests.helpers import assert_between, assert_in, assert_true


def test_public_api_indexes_assesses_edits_and_verifies(pdf, tmp_path):
    with squidpdf.MuPDFEngine(pdf) as eng:
        index = eng.index()
        reports = eng.assess(index)
        assert_between(squidpdf.green_rate(reports), 0.0, 1.0, "green rate on this fixture")

        span = next(s for s in index if "14 March 2026" in s.text)
        log = squidpdf.EditLog([squidpdf.Replace(span.id, "2 April 2026")])
        apply(eng, log.edits, index)

        redact_span = next(s for s in index if s.page == 1)
        redactions = [squidpdf.Redact(redact_span.id)]
        apply(eng, redactions, index)

        out = tmp_path / "edited.pdf"
        eng.save(str(out))
        verified = verify_redactions(eng, redactions, index)[redact_span.id]
        assert_true(verified is True, "verify_redactions() result for the redacted span")

    edited = pymupdf.open(out)[span.page].get_text()
    assert_in("2 April 2026", edited, "the saved page after a replace")
