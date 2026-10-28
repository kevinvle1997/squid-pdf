"""What the PDF says, and what we can promise about changing it.

The only way in from outside `core`, so what a feature uses is exported on purpose;
nothing outside names the PDF library (open a PDF with `open_pdf`), and nothing here
imports a feature. tests/test_layers.py checks all three.
"""

from __future__ import annotations

from squidpdf.core.app import words as words
from squidpdf.core.app.errors import (
    Damaged as Damaged,
    ErrorController as ErrorController,
    Failure as Failure,
    InvalidRequest as InvalidRequest,
    NotFound as NotFound,
    Problem as Problem,
    TooHeavy as TooHeavy,
)
from squidpdf.core.app.logs import (
    ERROR as ERROR,
    INFO as INFO,
    WARN as WARN,
    LogController as LogController,
    LogEvent as LogEvent,
    OwnText as OwnText,
    Tallies as Tallies,
    Tally as Tally,
    WorkerAnswer as WorkerAnswer,
    add_from_worker as add_from_worker,
    carried as carried,
    current_request_id as current_request_id,
    in_request as in_request,
    ms_since as ms_since,
    start_request as start_request,
    tallies_carried_by as tallies_carried_by,
    tally as tally,
    tally_fields as tally_fields,
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
    ANALYSIS_TUNING as ANALYSIS_TUNING,
    CONDENSE_LIMIT as CONDENSE_LIMIT,
    GREEN_RATE_TARGET as GREEN_RATE_TARGET,
    GREEN_RATE_WARN as GREEN_RATE_WARN,
    OPTION_KEYS as OPTION_KEYS,
    ROOM_SLACK_PT as ROOM_SLACK_PT,
    SHRINK_FLOOR as SHRINK_FLOOR,
    TOLERANCE_PT as TOLERANCE_PT,
)
from squidpdf.core.engine import (
    BUILD as BUILD,
    CORE_ERRORS as CORE_ERRORS,
    DEFAULT_DRIVER_FACTORY as DEFAULT_DRIVER_FACTORY,
    Engine as Engine,
    LineToDraw as LineToDraw,
    face_widths as face_widths,
    open_pdf as open_pdf,
    result_of as result_of,
)
from squidpdf.core.fit import SubstituteWidths as SubstituteWidths
from squidpdf.core.fonts.catalog import CATALOG as CATALOG, FACES as FACES
from squidpdf.core.fonts.document import FontSources as FontSources
from squidpdf.core.fonts.google import google_fonts as google_fonts
from squidpdf.core.fonts.loggable import loggable_family as loggable_family
from squidpdf.core.fonts.look_alike import broadest as broadest
from squidpdf.core.fonts.names import strip_subset as strip_subset
from squidpdf.core.fonts.substitute import face_coverage as face_coverage
from squidpdf.core.pdf.driver import DriverFactory as DriverFactory
from squidpdf.core.pdf.samples import write_dense as write_dense, write_sample as write_sample
from squidpdf.core.plan import DrawPlan as DrawPlan
from squidpdf.core.redaction import (
    RedactionCheck as RedactionCheck,
    any_word_left as any_word_left,
)
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
    QUARTER_TURNS as QUARTER_TURNS,
    Category as Category,
    Face as Face,
    Fragment as Fragment,
    HiddenPlace as HiddenPlace,
    Page as Page,
    QuarterTurn as QuarterTurn,
    Rect as Rect,
    Span as Span,
    SpanIndex as SpanIndex,
    Style as Style,
    index_as_json as index_as_json,
    index_from_json as index_from_json,
    index_of as index_of,
    new_text as new_text,
    pages_as_json as pages_as_json,
    pages_from_json as pages_from_json,
    turn_added as turn_added,
)
