"""What render and export worked out, as the browser gets it: in the reader's words."""

from __future__ import annotations

from typing import assert_never

from squidpdf.core import words
from squidpdf.editing.fit import FitReport
from squidpdf.editing.types import (
    FileNotice,
    FitInfo,
    InsertNotice,
    Notice,
    NoticeInfo,
    Skipped,
    SkippedInfo,
    SpanNotice,
)

__all__ = [
    "fit_info",
    "skipped_info",
    "notice_info",
]


def fit_info(fit: FitReport, said_in: str) -> FitInfo:
    """A fit as the browser gets it: option names, since it has their sentences."""
    parts = fit.describe()
    return {
        "delta_pt": fit.delta_pt,
        "missing": fit.missing,
        "left_out": fit.left_out,
        "options": [option.name for option in fit.options],
        "strategy": fit.strategy,
        "message": words.render_all(parts, said_in),
        "message_parts": [part.as_info() for part in parts],
    }


def skipped_info(skipped: Skipped, said_in: str) -> SkippedInfo:
    """An edit left out, as the browser gets it: why, in words and unsaid."""
    return {"edit": skipped.edit, "type": skipped.type, **words.said(skipped.detail, said_in)}


def notice_info(notice: Notice, said_in: str) -> NoticeInfo:
    """What came out other than asked, as the browser gets it: why, in words and unsaid.

    `kind` says what it's about, so the browser knows which name to read.
    """
    said = words.said(notice.detail, said_in)
    # A replace or a redaction: named by its span.
    if isinstance(notice, SpanNotice):
        return {"kind": "span", "span_id": notice.span_id, **said}
    # An insert, which has no span: named by its place in the list the browser sent.
    if isinstance(notice, InsertNotice):
        return {"kind": "insert", "edit": notice.edit, **said}
    # The whole file: nothing to name.
    if isinstance(notice, FileNotice):
        return {"kind": "file", **said}
    assert_never(notice)
