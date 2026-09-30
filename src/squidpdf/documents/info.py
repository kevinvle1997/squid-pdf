"""What the browser gets for a document: the analysis, in the reader's words."""

from __future__ import annotations

from datetime import UTC, datetime

from squidpdf.core import Message, words
from squidpdf.core.constants import CONDENSE_LIMIT, SHRINK_FLOOR, TOLERANCE_PT
from squidpdf.documents.types import (
    Analysis,
    Copy,
    Document,
    DocumentNoticeInfo,
    FontFacts,
    FontInfo,
)

__all__ = [
    "document_response",
]

# Every way a span can be approximate: the sentences behind a span's `why` code.
_APPROXIMATE_KEYS = ("turned_text", "spaced_text")


def document_response(
    doc_id: str, *, expires_at: float, analysis: Analysis, said_in: str
) -> Document:
    """The analysis, plus what belongs to this document and this moment, in `said_in`."""
    return {
        "build": analysis["build"],
        "pages": analysis["pages"],
        "spans": analysis["spans"],
        "fonts": [font_info(font, said_in) for font in analysis["fonts"]],
        "id": doc_id,
        "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
        "fit": {
            "tolerance_pt": TOLERANCE_PT,
            "condense_limit": CONDENSE_LIMIT,
            "shrink_floor": SHRINK_FLOOR,
        },
        "copy": copy_in(said_in),
        "notices": notices_in(analysis, said_in),
    }


def font_info(font: FontFacts, said_in: str) -> FontInfo:
    """A font as the browser gets it: why its own copy can't be used, in `said_in`."""
    why = None if font["why"] is None else Message.from_info(font["why"])
    return {
        "name": font["name"],
        "substitute": font["substitute"],
        "why": None if why is None else words.render(why, said_in),
        "why_code": None if why is None else why.key,
        "why_params": {} if why is None else why.params,
        "same_widths": font["same_widths"],
        "glyphs": font["glyphs"],
    }


def copy_in(said_in: str) -> Copy:
    """The sentences the browser fills in as the user types, in `said_in`, unfilled."""
    options = {
        name: {part: words.sentence(key, said_in) for part, key in keys.items()}
        for name, keys in words.OPTION_KEYS.items()
    }
    return {
        "missing": words.sentence("missing", said_in),
        "too_long": words.sentence("too_long", said_in),
        "stand_in": words.sentence("stand_in", said_in),
        "stand_in_same_widths": words.sentence("stand_in_same_widths", said_in),
        "undo_redaction": words.sentence("undo_redaction", said_in),
        "options": options,
        "approximate": {key: words.sentence(key, said_in) for key in _APPROXIMATE_KEYS},
    }


def notices_in(analysis: Analysis, said_in: str) -> list[DocumentNoticeInfo]:
    """What may not be what the user expected of this document, in `said_in`."""
    # A scan has no text layer: say so, rather than show a page nothing on can be edited.
    if analysis["spans"]:
        return []
    no_text = Message("no_text")
    return [{"type": "no_text", "detail": words.render(no_text, said_in), **no_text.as_info()}]
