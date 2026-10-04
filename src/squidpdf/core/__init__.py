"""What the PDF says, and what we can promise about changing it.

Imported by every feature, through this module only: nothing outside `core`
imports one of its modules, so what a feature uses is exported here on purpose,
and nothing outside names the PDF library (open a PDF with `open_pdf`). Nothing
here may import a feature. tests/test_layers.py checks all three.
"""

from __future__ import annotations

from squidpdf.core.app import words as words
from squidpdf.core.app.errors import (
    Damaged as Damaged,
    Encrypted as Encrypted,
    ErrorController as ErrorController,
    Failure as Failure,
    InvalidRequest as InvalidRequest,
    NotFound as NotFound,
    Problem as Problem,
    TooHeavy as TooHeavy,
    Unreadable as Unreadable,
)
from squidpdf.core.app.message import (
    Message as Message,
    MessageInfo as MessageInfo,
    Param as Param,
    SaidInfo as SaidInfo,
)
from squidpdf.core.app.reply import Reply as Reply
from squidpdf.core.app.workers import Workers as Workers
from squidpdf.core.constants import (
    CONDENSE_LIMIT as CONDENSE_LIMIT,
    FIDELITY_TUNING as FIDELITY_TUNING,
    GREEN_RATE_TARGET as GREEN_RATE_TARGET,
    GREEN_RATE_WARN as GREEN_RATE_WARN,
    OPTION_KEYS as OPTION_KEYS,
    SHRINK_FLOOR as SHRINK_FLOOR,
    TOLERANCE_PT as TOLERANCE_PT,
)
from squidpdf.core.engine import Engine as Engine, LineToDraw as LineToDraw
from squidpdf.core.fonts.catalog import CATALOG as CATALOG, FACES as FACES
from squidpdf.core.fonts.document import FontSources as FontSources
from squidpdf.core.fonts.google import google_fonts as google_fonts

# The one import that names the driver: another PDF library is swapped in here.
from squidpdf.core.pdf.mupdf import (
    BUILD as BUILD,
    CORE_ERRORS as CORE_ERRORS,
    face_widths as face_widths,
    open_pdf as open_pdf,
    result_of as result_of,
)
from squidpdf.core.pdf.samples import write_dense as write_dense, write_sample as write_sample
from squidpdf.core.plan import DrawPlan as DrawPlan
from squidpdf.core.text.fidelity import (
    APPROXIMATE_REASONS as APPROXIMATE_REASONS,
    ApproximateReason as ApproximateReason,
    Fidelity as Fidelity,
    FidelityReport as FidelityReport,
    green_rate as green_rate,
    reason_of as reason_of,
)
from squidpdf.core.types import (
    COPY_PLACES as COPY_PLACES,
    Category as Category,
    Fragment as Fragment,
    HiddenPlace as HiddenPlace,
    Page as Page,
    QuarterTurn as QuarterTurn,
    Rect as Rect,
    Span as Span,
    SpanIndex as SpanIndex,
    Style as Style,
    index_of as index_of,
    new_text as new_text,
)
