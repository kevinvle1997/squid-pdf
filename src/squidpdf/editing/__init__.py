"""Changing text, and removing it.

Replace is remove-then-redraw; Redact is remove-and-stop. Same machinery, so one
module owns both.
"""

from squidpdf.editing.apply import BadReference, apply, check, fits, verify_redactions
from squidpdf.editing.edits import Edit, EditLog, Insert, Redact, Replace
from squidpdf.editing.fit import FitCheck, Option, options_for
from squidpdf.editing.types import Skipped, Strategy

__all__ = [
    "BadReference",
    "apply",
    "check",
    "fits",
    "verify_redactions",
    "Edit",
    "EditLog",
    "Insert",
    "Redact",
    "Replace",
    "FitCheck",
    "Option",
    "options_for",
    "Skipped",
    "Strategy",
]
