"""Changing text, and removing it.

Replace is remove-then-redraw; Redact is remove-and-stop. Same machinery, so one
module owns both.
"""

from squidpdf.editing.apply import (
    apply,
    insert_fit,
    log_fits,
    replace_fit,
)
from squidpdf.editing.edits import Edit, EditLog, Insert, Redact, Replace
from squidpdf.editing.errors import BadReference, RedactionFailed
from squidpdf.editing.fit import FitReport, LogFits, Option, options_for
from squidpdf.editing.redaction import RedactionController
from squidpdf.editing.types import Applied, Notice, Skipped, Strategy

__all__ = [
    "BadReference",
    "RedactionFailed",
    "RedactionController",
    "apply",
    "replace_fit",
    "insert_fit",
    "log_fits",
    "Edit",
    "EditLog",
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
