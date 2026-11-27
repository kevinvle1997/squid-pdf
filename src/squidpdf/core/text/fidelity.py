"""Apart from `Span`: a Span never changes, and fidelity changes with our fonts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from squidpdf.core.app.message import Message, Param

# Pydantic reads it: a plain alias, not `type`.
Fidelity = Literal[
    "exact",
    "approximate",
    "substitute",
]

ApproximateReason = Literal[
    "turned_text", "right_to_left_text", "spaced_text", "undrawable_letters"
]
APPROXIMATE_REASONS: tuple[ApproximateReason, ...] = get_args(ApproximateReason)


@dataclass(frozen=True, slots=True)
class FidelityReport:
    span_id: str
    state: Fidelity
    font: str
    in_file: bool
    substitute: str | None = None
    why: Message | None = None
    same_widths: bool = False
    letter_spacing: float = 0.0


def why_approximate(
    reason: ApproximateReason, params: dict[str, Param] | None = None
) -> Message:
    return Message(reason, params or {})


def reason_of(why: Message) -> ApproximateReason:
    for reason in APPROXIMATE_REASONS:
        if why.key == reason:
            return reason
    raise ValueError(f"not a way a span can be approximate: {why.key!r}")


def green_rate(reports: list[FidelityReport]) -> float | None:
    """None when there are no spans: 0% would read as every edit failing."""
    if not reports:
        return None
    exact_count = sum(1 for report in reports if report.state == "exact")
    return exact_count / len(reports)
