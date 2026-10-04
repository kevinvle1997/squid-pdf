"""What render and export worked out, as the browser gets it: in the reader's words."""

from __future__ import annotations

from typing import assert_never

from squidpdf.core import COPY_PLACES, Message, words
from squidpdf.editing.fit import FitReport
from squidpdf.editing.types import (
    FileNotice,
    FitInfo,
    InsertNotice,
    Notice,
    NoticeInfo,
    Redaction,
    RedactionInfo,
    Skipped,
    SkippedInfo,
    SpanNotice,
)


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


def redaction_info(redaction: Redaction, said_in: str) -> RedactionInfo:
    """A redaction drawn, as the browser gets it: its places by name, and what matters in words.

    Words still in the file are said alone: the download is refused, so the
    places it would leave them out of don't matter yet.
    """
    return {
        "verified": redaction.verified,
        "hidden_copies": redaction.hidden_copies,
        "message": words.render_all(_said_of(redaction, said_in), said_in),
    }


def _said_of(redaction: Redaction, said_in: str) -> list[Message]:
    """What to say of a redaction drawn: its words still there, else where else they were.

    And that a signed file's signatures go, which aren't a place of a copy.
    """
    # Still in the file: the download is refused.
    if not redaction.verified:
        return [Message("not_redacted")]
    copy_places = [place for place in redaction.hidden_copies if place in COPY_PLACES]
    named = [words.sentence(f"place_{place}", said_in) for place in copy_places]
    said = [Message("hidden_copies", {"places": named})] if named else []
    # A signed file: the redaction takes every signature, whatever it holds.
    if "signatures" in redaction.hidden_copies:
        said.append(Message("signatures_removed"))
    return said


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
