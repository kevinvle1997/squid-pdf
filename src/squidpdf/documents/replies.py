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
    CountedSentence,
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
        "signed": facts["signed"],
        "id": doc_id,
        "expires_at": time_of(expires_at),
        "fit": _fit_rules(),
        "sentences": _sentences_in(language),
        "notices": _notices_in(
            has_text=kept.spans != _NO_SPANS,
            active_content_dropped=facts["active_content_dropped"],
            signed=facts["signed"],
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
        "font_object": font["font_object"],
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
        "not_in_font": words.sentence("not_in_font", language),
        "will_leave_out": words.sentence("will_leave_out", language),
        "too_long": words.sentence("too_long", language),
        "substitute": words.sentence("substitute", language),
        "substitute_same_widths": words.sentence("substitute_same_widths", language),
        "drawn_in_your_copy": words.sentence("drawn_in_your_copy", language),
        "drawn_in_own_copy": words.sentence("drawn_in_own_copy", language),
        "undo_redaction": words.sentence("undo_redaction", language),
        "reopened": words.sentence("reopened", language),
        "export_left_out": words.sentence("export_left_out", language),
        "page_left_out": words.sentence("page_left_out", language),
        "page_put_back": words.sentence("page_put_back", language),
        "form_field_not_edited": words.sentence("form_field_not_edited", language),
        "empty": words.sentence("empty", language),
        "edit_breaks_signature": words.sentence("edit_breaks_signature", language),
        "email_broken": words.sentence("email_broken", language),
        "back_to": words.sentence("back_to", language),
        "changed_to": words.sentence("changed_to", language),
        "put_back": words.sentence("put_back", language),
        "redacted": words.sentence("redacted", language),
        "your_copy_in_use": words.sentence("your_copy_in_use", language),
        "your_copy_removed": words.sentence("your_copy_removed", language),
        "warning_muted": words.sentence("warning_muted", language),
        "warning_unmuted": words.sentence("warning_unmuted", language),
        "downloaded": words.sentence("downloaded", language),
        "find_nothing": words.sentence("find_nothing", language),
        "find_identical": _counted_in("find_identical", language),
        "find_different": _counted_in("find_different", language),
        "find_places": _counted_in("find_places", language),
        "find_some_different": _counted_in("find_some_different", language),
        "changed_places": _counted_in("changed_places", language),
        "changes": _counted_in("changes", language),
        "changes_different": _counted_in("changes_different", language),
        "changes_similar": _counted_in("changes_similar", language),
        "changes_some": _counted_in("changes_some", language),
        "changes_some_different": _counted_in("changes_some_different", language),
        "changes_some_similar": _counted_in("changes_some_similar", language),
        "at_rest_different": _counted_in("at_rest_different", language),
        "at_rest_similar": _counted_in("at_rest_similar", language),
        "changes_too_long": _counted_in("changes_too_long", language),
        "typed_identical": words.sentence("typed_identical", language),
        "typed_different": words.sentence("typed_different", language),
        "typed_similar": words.sentence("typed_similar", language),
        "join_parts": words.sentence("join_parts", language),
        "options": options,
        # Every way a span can be approximate: the sentences behind a span's `why` code.
        "approximate": {
            reason: words.sentence(reason, language) for reason in APPROXIMATE_REASONS
        },
        "characters": words.character_names(language),
    }


def _counted_in(key: str, language: str) -> CountedSentence:
    """The sentence under `key`, said with a count, in each form, in `language`."""
    forms = words.forms(key, language)
    return {"one": forms["one"], "other": forms["other"]}


def _notices_in(
    *, has_text: bool, active_content_dropped: bool, signed: bool, language: str
) -> list[DocumentNoticeInfo]:
    """What may not be what the user expected of this document, in `language`."""
    said: list[Message] = []
    # A scan has no text layer: say so, rather than show a page nothing on can be edited.
    if not has_text:
        said.append(Message("no_text"))
    # Taken out when it came in: the file downloaded lacks what the user's had.
    if active_content_dropped:
        said.append(Message("active_content_dropped"))
    # Any download from here is a new file, so no signature in it holds: said before the edit.
    if signed:
        said.append(Message("signed_file"))
    return [{"type": message.key, **words.said(message, language)} for message in said]
