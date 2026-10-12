"""Changing text, removing it, adding new text, and turning, leaving out or moving pages.

Every edit goes through one list and one machinery, so one package owns them all.
"""

from __future__ import annotations

from squidpdf.editing.apply import (
    apply_edits as apply_edits,
    log_fits as log_fits,
    resolve as resolve,
)
from squidpdf.editing.edits import (
    DeletePage as DeletePage,
    Edit as Edit,
    Insert as Insert,
    MovePage as MovePage,
    PageEdit as PageEdit,
    PageOp as PageOp,
    Redact as Redact,
    Replace as Replace,
    RotatePage as RotatePage,
    SpanEdit as SpanEdit,
    Strategy as Strategy,
)
from squidpdf.editing.errors import (
    BadReference as BadReference,
    RedactionConflict as RedactionConflict,
    RedactionFailed as RedactionFailed,
)
from squidpdf.editing.export import (
    ExportController as ExportController,
    save_edited as save_edited,
)
from squidpdf.editing.fit import (
    FitReport as FitReport,
    Fitted as Fitted,
    LogFits as LogFits,
    Option as Option,
    insert_fit as insert_fit,
    options_for as options_for,
    replace_fit as replace_fit,
)
from squidpdf.editing.redaction import RedactionController as RedactionController
from squidpdf.editing.types import (
    Applied as Applied,
    Notice as Notice,
    Skipped as Skipped,
)
