"""What the browser gets for a document: the analysis, in the reader's words."""

from __future__ import annotations

from datetime import UTC, datetime

import orjson
import xxhash

from squidpdf.core import (
    APPROXIMATE_REASONS,
    CONDENSE_LIMIT,
    OPTION_KEYS,
    ROOM_SLACK_PT,
    SHRINK_FLOOR,
    TOLERANCE_PT,
    Message,
    words,
)
from squidpdf.documents import constants
from squidpdf.documents.types import (
    AnalysisFacts,
    Copy,
    Document,
    DocumentNoticeInfo,
    FitRules,
    FontFacts,
    FontInfo,
    KeptAnalysis,
)

_NO_SPANS = b"[]"  # the spans kept for a file with no text, as a scan


def document_json(doc_id: str, *, expires_at: float, kept: KeptAnalysis, said_in: str) -> bytes:
    """The document as the browser gets it, as JSON: the kept analysis, said in `said_in`.

    The spans go out exactly as kept, never read: on a long document they are
    nearly all of it, and reading them and writing them out again held up the
    server. The rest is read as usual: the pages' sizes and the fonts, small
    beside them.
    """
    facts: AnalysisFacts = orjson.loads(kept.facts)
    body: Document = {
        "build": facts["build"],
        "pages": facts["pages"],
        "spans": [],  # sent as kept, below
        "fonts": [_font_info(font, said_in) for font in facts["fonts"]],
        "id": doc_id,
        "expires_at": time_of(expires_at),
        "fit": _fit_rules(),
        "copy": _copy_in(said_in),
        "notices": _notices_in(has_text=kept.spans != _NO_SPANS, said_in=said_in),
    }
    return orjson.dumps({**body, "spans": orjson.Fragment(kept.spans)})


def reply_digest(said_in: str) -> str:
    """A digest of the words, fit rules and shape of a document's reply.

    Worked out on every read: a deploy changing any of them, with no new build, is a new body.
    """
    rules = orjson.dumps([constants.REPLY_VERSION, _fit_rules()])
    catalog = orjson.dumps([said_in, words.catalog(said_in)])
    return xxhash.xxh3_64_hexdigest(rules + catalog)


def time_of(epoch_seconds: float) -> str:
    """A moment as the browser reads it: ISO 8601, in UTC."""
    return datetime.fromtimestamp(epoch_seconds, UTC).isoformat()


def _fit_rules() -> FitRules:
    """The thresholds the browser runs the fit check with: the server's own."""
    return {
        "tolerance_pt": TOLERANCE_PT,
        "room_slack_pt": ROOM_SLACK_PT,
        "condense_limit": CONDENSE_LIMIT,
        "shrink_floor": SHRINK_FLOOR,
    }


def _font_info(font: FontFacts, said_in: str) -> FontInfo:
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


def _copy_in(said_in: str) -> Copy:
    """The sentences the browser fills in as the user types, in `said_in`, unfilled."""
    options = {
        name: {
            "label": words.sentence(keys.label, said_in),
            "detail": words.sentence(keys.detail, said_in),
        }
        for name, keys in OPTION_KEYS.items()
    }
    return {
        "missing": words.sentence("missing", said_in),
        "too_long": words.sentence("too_long", said_in),
        "substitute": words.sentence("substitute", said_in),
        "substitute_same_widths": words.sentence("substitute_same_widths", said_in),
        "undo_redaction": words.sentence("undo_redaction", said_in),
        "reopened": words.sentence("reopened", said_in),
        "export_left_out": words.sentence("export_left_out", said_in),
        "form_field_not_edited": words.sentence("form_field_not_edited", said_in),
        "options": options,
        # Every way a span can be approximate: the sentences behind a span's `why` code.
        "approximate": {
            reason: words.sentence(reason, said_in) for reason in APPROXIMATE_REASONS
        },
    }


def _notices_in(*, has_text: bool, said_in: str) -> list[DocumentNoticeInfo]:
    """What may not be what the user expected of this document, in `said_in`."""
    # A scan has no text layer: say so, rather than show a page nothing on can be edited.
    if has_text:
        return []
    return [{"type": "no_text", **words.said(Message("no_text"), said_in)}]
