"""Whether an edit will look identical, decided before the user commits.

This is the product. Everything else is a text editor.

Kept apart from `Span` on purpose: a Span is what the PDF says, and that does not
change. Fidelity is what *we* can promise given the fonts we ship, which changes
when the library does. Joining them would make the span index go stale for a
reason that has nothing to do with the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from squidpdf.core.app.message import Message, Param

# The ways an edit can turn out, in terms of the original font. Pydantic reads it: no `type`.
Fidelity = Literal[
    "exact",  # the document's own font is in the file and covers it
    "approximate",  # the file's own font draws it, but not as the page shows it: `why` says how
    "substitute",  # the file's own copy can't be used, so another face draws
]

# Each way the file's own font draws an edit unlike the page, by the key of the sentence
# that says it: an approximate span's `why`. A plain alias: pydantic reads it.
ApproximateReason = Literal["turned_text", "spaced_text", "undrawable_letters"]
APPROXIMATE_REASONS: tuple[ApproximateReason, ...] = get_args(ApproximateReason)


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


def why_approximate(
    reason: ApproximateReason, params: dict[str, Param] | None = None
) -> Message:
    """Why a span is approximate: the sentence `reason` names, and the facts it takes."""
    return Message(reason, params or {})


def reason_of(why: Message) -> ApproximateReason:
    """The reason an approximate span's `why` names.

    Raises ValueError for a Message that names none: `why_approximate` makes every one.
    """
    for reason in APPROXIMATE_REASONS:
        if why.key == reason:
            return reason
    raise ValueError(f"not a way a span can be approximate: {why.key!r}")


def green_rate(reports: list[FidelityReport]) -> float:
    """The share of spans that keep their original font.

    The one number the product is judged on. Below GREEN_RATE_TARGET the
    promise inverts: substitution becomes the normal case and the signal reads
    as an apology rather than reassurance.
    """
    if not reports:
        return 0.0
    exact_count = sum(1 for report in reports if report.state == "exact")
    return exact_count / len(reports)
