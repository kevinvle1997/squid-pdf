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
    Document,
    DocumentNoticeInfo,
    FitRules,
    FontFacts,
    FontInfo,
    KeptAnalysis,
    Sentences,
)

_NO_SPANS = b"[]"  # the spans kept for a file with no text, as a scan


def document_json(
    doc_id: str, *, expires_at: float, kept: KeptAnalysis, language: str
) -> bytes:
    """The document as the browser gets it, as JSON: the kept analysis, said in `language`.

    The spans go out exactly as kept, never read: on a long document they're nearly all of it,
    and reading and rewriting them held up the server.
    """
    facts: AnalysisFacts = orjson.loads(kept.facts)
    body: Document = {
        "build": facts["build"],
        "pages": facts["pages"],
        "spans": [],  # sent as kept, below
        "fonts": [_font_info(font, language) for font in facts["fonts"]],
        "id": doc_id,
        "expires_at": time_of(expires_at),
        "fit": _fit_rules(),
        "sentences": _sentences_in(language),
        "notices": _notices_in(
            has_text=kept.spans != _NO_SPANS,
            active_content_dropped=facts["active_content_dropped"],
            language=language,
        ),
    }
    return orjson.dumps({**body, "spans": orjson.Fragment(kept.spans)})


def reply_digest(language: str) -> str:
    """A digest of the words, fit rules and shape of a document's reply.

    Worked out on every read: a deploy changing any of them, with no new build, is a new body.
    """
    rules = orjson.dumps([constants.REPLY_VERSION, _fit_rules()])
    catalog = orjson.dumps([language, words.catalog(language)])
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


def _font_info(font: FontFacts, language: str) -> FontInfo:
    """A font as the browser gets it: why its own copy can't be used, in `language`."""
    why = None if font["why"] is None else Message.from_info(font["why"])
    return {
        "name": font["name"],
        "shown_name": font["shown_name"],
        "substitute": font["substitute"],
        "why": None if why is None else words.render(why, language),
        "why_code": None if why is None else why.key,
        "why_params": {} if why is None else why.params,
        "same_widths": font["same_widths"],
        "look_alike": font["look_alike"],
        "attached": font["attached"],
        "glyphs": font["glyphs"],
    }


def _sentences_in(language: str) -> Sentences:
    """The sentences the browser says about the document, in `language`, unfilled."""
    options = {
        name: {
            "label": words.sentence(keys.label, language),
            "detail": words.sentence(keys.detail, language),
        }
        for name, keys in OPTION_KEYS.items()
    }
    return {
        "missing": words.sentence("missing", language),
        "will_leave_out": words.sentence("will_leave_out", language),
        "too_long": words.sentence("too_long", language),
        "substitute": words.sentence("substitute", language),
        "substitute_same_widths": words.sentence("substitute_same_widths", language),
        "drawn_in_your_copy": words.sentence("drawn_in_your_copy", language),
        "drawn_in_own_copy": words.sentence("drawn_in_own_copy", language),
        "undo_redaction": words.sentence("undo_redaction", language),
        "reopened": words.sentence("reopened", language),
        "export_left_out": words.sentence("export_left_out", language),
        "form_field_not_edited": words.sentence("form_field_not_edited", language),
        "empty": words.sentence("empty", language),
        "options": options,
        # Every way a span can be approximate: the sentences behind a span's `why` code.
        "approximate": {
            reason: words.sentence(reason, language) for reason in APPROXIMATE_REASONS
        },
        "characters": words.character_names(language),
    }


def _notices_in(
    *, has_text: bool, active_content_dropped: bool, language: str
) -> list[DocumentNoticeInfo]:
    """What may not be what the user expected of this document, in `language`."""
    said: list[Message] = []
    # A scan has no text layer: say so, rather than show a page nothing on can be edited.
    if not has_text:
        said.append(Message("no_text"))
    # Taken out when it came in: the file downloaded lacks what the user's had.
    if active_content_dropped:
        said.append(Message("active_content_dropped"))
    return [{"type": message.key, **words.said(message, language)} for message in said]
