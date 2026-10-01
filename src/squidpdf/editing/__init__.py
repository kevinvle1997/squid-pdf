"""Changing text, and removing it.

Replace is remove-then-redraw; Redact is remove-and-stop. Same machinery, so one
module owns both.
"""

from __future__ import annotations

from squidpdf.editing.apply import (
    apply_edits,
    insert_fit,
    log_fits,
    replace_fit,
    resolve,
)
from squidpdf.editing.edits import Edit, Insert, PageEdit, Redact, Replace, SpanEdit
from squidpdf.editing.errors import BadReference, RedactionConflict, RedactionFailed
from squidpdf.editing.export import ExportController, save_edited
from squidpdf.editing.fit import FitReport, LogFits, Option, options_for
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import Applied, Notice, Skipped, Strategy

__all__ = [
    "BadReference",
    "RedactionConflict",
    "RedactionFailed",
    "RedactionController",
    "ExportController",
    "resolve",
    "apply_edits",
    "save_edited",
    "replace_fit",
    "insert_fit",
    "log_fits",
    "Edit",
    "SpanEdit",
    "PageEdit",
    "Insert",
    "Redact",
    "Replace",
    "FitReport",
    "LogFits",
    "Option",
    "options_for",
    "Applied",
    "Notice",
    "Skipped",
    "Strategy",
]
