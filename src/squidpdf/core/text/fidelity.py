"""Whether an edit will look identical, decided before the user commits.

This is the product. Everything else is a text editor.

Kept apart from `Span` on purpose: a Span is what the PDF says, and that does not
change. Fidelity is what *we* can promise given the fonts we ship, which changes
when the library does. Joining them would make the span index go stale for a
reason that has nothing to do with the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from squidpdf.core.app.message import Message


class Fidelity(StrEnum):
    """The ways an edit can turn out, in terms of the original font."""

    EXACT = "exact"  # the document's own font is in the file and covers it
    # The file's own font draws it, but not as the page shows it now: `why` says how.
    APPROXIMATE = "approximate"
    SUBSTITUTE = "substitute"  # the file's own copy can't be used; another face draws
    IMAGE = "image"  # no text layer here at all


@dataclass(frozen=True, slots=True)
class FidelityReport:
    """What we can say about one span: facts, and a Message the interface puts into words."""

    span_id: str
    state: Fidelity
    font: str
    in_file: bool  # the file's own copy of the font can be used, if only for some letters
    substitute: str | None = None  # the face we ship that draws it, e.g. "Carlito Bold"
    why: Message | None = (
        None  # why it isn't exact: the own font can't be used, or how it differs
    )
    same_widths: bool = False  # the substitute's letters are as wide, so nothing moves


def green_rate(reports: list[FidelityReport]) -> float:
    """The share of spans that keep their original font.

    The one number the product is judged on. Below GREEN_RATE_TARGET the
    promise inverts: substitution becomes the normal case and the signal reads
    as an apology rather than reassurance.
    """
    if not reports:
        return 0.0
    exact_count = sum(1 for report in reports if report.state is Fidelity.EXACT)
    return exact_count / len(reports)
