"""What render and export worked out, as the browser gets it: in the reader's words."""

from __future__ import annotations

from typing import assert_never

from squidpdf.core import COPY_PLACES, Message, RestOfLine, reason_of, words
from squidpdf.documents.types import ApproximateInfo
from squidpdf.editing.fit import FitReport, Verdict
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


def fit_info(fit: FitReport, verdict: Verdict, language: str) -> FitInfo:
    """A fit and its edit's verdict, as the browser gets them: option names, not sentences."""
    parts = fit.describe()
    substituted = verdict.fidelity == "substitute"
    return {
        "delta_pt": fit.delta_pt,
        "missing": fit.missing,
        "left_out": fit.left_out,
        "options": [option.name for option in fit.options],
        "strategy": fit.strategy,
        "message": words.render_all(parts, language),
        "message_parts": [part.as_info() for part in parts],
        "fidelity": verdict.fidelity,
        "why": _approximate_why(verdict),
        "substitute": verdict.substitute.face.name if substituted else None,
        "same_widths": substituted and verdict.substitute.same_widths,
    }


def _approximate_why(verdict: Verdict) -> ApproximateInfo | None:
    """How an approximate edit won't match the text around it, in no language; else None."""
    if verdict.why is None:
        return None
    return {"code": reason_of(verdict.why), "params": verdict.why.params}


def redaction_info(redaction: Redaction, language: str) -> RedactionInfo:
    """A redaction drawn, as the browser gets it: its places by name, and what to say of it."""
    return {
        "verified": redaction.verified,
        "hidden_copies": redaction.hidden_copies,
        "message": words.render_all(_said_of(redaction, language), language),
    }


def _said_of(redaction: Redaction, language: str) -> list[Message]:
    """What to say of a redaction drawn: its words still there, else what else it changed."""
    # Still in the file: the download is refused.
    if not redaction.verified:
        return [why_unredacted(redaction)]
    # What the erase did to the rest of its line: said first, as it's on the page.
    said = rest_of_line_said(redaction.rest)
    copy_places = [place for place in redaction.hidden_copies if place in COPY_PLACES]
    named = [words.sentence(f"place_{place}", language) for place in copy_places]
    if named:
        said.append(Message("hidden_copies", {"places": named}))
    # A signed file: the redaction takes every signature, whatever it holds.
    if "signatures" in redaction.hidden_copies:
        said.append(Message("signatures_removed"))
    return said


def rest_of_line_said(rest: RestOfLine) -> list[Message]:
    """What to say of what a redaction's erase did to the rest of its line; nothing in place."""
    if rest == "in_place":
        return []
    if rest == "moved":
        return [Message("rest_of_line_moved")]
    if rest == "removed":
        return [Message("rest_of_line_removed")]
    assert_never(rest)


def why_unredacted(redaction: Redaction) -> Message:
    """Why a redaction's words are still in the file: new text drawn over it, or not removed."""
    # What the check read in the box may be the new text's: the user's to move.
    if redaction.covered:
        return Message("covered_by_new_text")
    return Message("not_redacted")


def skipped_info(skipped: Skipped, language: str) -> SkippedInfo:
    """An edit left out, as the browser gets it: why, in words and unsaid."""
    return {"edit": skipped.edit, "type": skipped.type, **words.said(skipped.detail, language)}


def notice_info(notice: Notice, language: str) -> NoticeInfo:
    """What came out other than asked, as the browser gets it: why, in words and unsaid.

    `kind` says what it's about, so the browser knows which name to read.
    """
    said = words.said(notice.detail, language)
    # A replace or a redaction: named by its span.
    if isinstance(notice, SpanNotice):
        return {"kind": "span", "span_id": notice.span_id, **said}
    # An insert, which has no span: named by its place in the list the browser sent.
    if isinstance(notice, InsertNotice):
        return {"kind": "insert", "edit": notice.edit, **said}
    if isinstance(notice, FileNotice):
        return {"kind": "file", **said}
    assert_never(notice)
