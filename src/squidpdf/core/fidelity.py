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

GREEN_RATE_TARGET = 0.8  # below this, substitution is the normal case, not the exception
GREEN_RATE_WARN = 0.5  # below this, the CLI marks a document red rather than yellow


class Fidelity(StrEnum):
    """The three ways an edit can turn out, in terms of the original font."""

    EXACT = "exact"  # the document's own font is in the file and covers it
    SUBSTITUTE = "substitute"  # not embedded; a metric-compatible stand-in is used
    IMAGE = "image"  # no text layer here at all


@dataclass(frozen=True, slots=True)
class FidelityReport:
    """What we can say about one span, in terms the interface can show directly."""

    span_id: str
    state: Fidelity
    font: str
    substitute: str | None = None


def green_rate(reports: list[FidelityReport]) -> float:
    """The share of spans that keep their original font.

    The one number the product is judged on. Below GREEN_RATE_TARGET the
    promise inverts: substitution becomes the normal case and the signal reads
    as an apology rather than reassurance.
    """
    if not reports:
        return 0.0
    return sum(1 for r in reports if r.state is Fidelity.EXACT) / len(reports)
