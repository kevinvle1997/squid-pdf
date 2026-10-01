"""What render and export worked out, as the browser gets it: in the reader's words."""

from __future__ import annotations

from squidpdf.core import words
from squidpdf.editing.fit import FitReport
from squidpdf.editing.types import FitInfo, Notice, NoticeInfo, Skipped, SkippedInfo

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
    return {
        "edit": skipped.edit,
        "type": skipped.type,
        "detail": words.render(skipped.detail, said_in),
        **skipped.detail.as_info(),
    }


def notice_info(notice: Notice, said_in: str) -> NoticeInfo:
    """An edit drawn other than asked, as the browser gets it: why, in words and unsaid."""
    return {
        "span_id": notice.span_id,
        "detail": words.render(notice.detail, said_in),
        "edit": notice.edit,
        **notice.detail.as_info(),
    }
