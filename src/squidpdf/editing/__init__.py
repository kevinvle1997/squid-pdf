"""Changing text, removing it, and adding new text.

Replace is remove-then-redraw; Redact is remove-and-stop; Insert draws new text
where the document has none. Same machinery, so one package owns all three.
"""

from __future__ import annotations

from squidpdf.editing.apply import (
    apply_edits as apply_edits,
    insert_fit as insert_fit,
    log_fits as log_fits,
    replace_fit as replace_fit,
    resolve as resolve,
)
from squidpdf.editing.edits import (
    Edit as Edit,
    Insert as Insert,
    PageEdit as PageEdit,
    Redact as Redact,
    Replace as Replace,
    SpanEdit as SpanEdit,
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
    LogFits as LogFits,
    Option as Option,
    options_for as options_for,
)
from squidpdf.editing.redaction import RedactionController as RedactionController
from squidpdf.editing.types import (
    Applied as Applied,
    Notice as Notice,
    Skipped as Skipped,
    Strategy as Strategy,
)
