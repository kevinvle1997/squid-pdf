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

_NO_SPANS = b"[]"


def document_json(
    doc_id: str, *, expires_at: float, kept: KeptAnalysis, language: str
) -> bytes:
    """The spans go out as kept, never read: on a long document they're nearly all of it."""
    facts: AnalysisFacts = orjson.loads(kept.facts)
    body: Document = {
        "build": facts["build"],
        "pages": facts["pages"],
        "spans": [],
        "fonts": [_font_info(font, language) for font in facts["fonts"]],
        "lines": facts["lines"],
        "signed": facts["signed"],
        "id": doc_id,
        "expires_at": time_of(expires_at),
        "fit": _fit_rules(),
        "sentences": _sentences_in(language),
        "notices": _notices_in(
            has_text=kept.spans != _NO_SPANS,
            active_content_dropped=facts["active_content_dropped"],
            plain_xmp_dropped=facts["plain_xmp_dropped"],
            signed=facts["signed"],
            language=language,
        ),
    }
    return orjson.dumps({**body, "spans": orjson.Fragment(kept.spans)})


def reply_digest(language: str) -> str:
    """A deploy changing any of them, with no new build, is a new body."""
    rules = orjson.dumps([constants.REPLY_VERSION, _fit_rules()])
    catalog = orjson.dumps([language, words.catalog(language)])
    return xxhash.xxh3_64_hexdigest(rules + catalog)


def time_of(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, UTC).isoformat()


def _fit_rules() -> FitRules:
    return {
        "tolerance_pt": TOLERANCE_PT,
        "room_slack_pt": ROOM_SLACK_PT,
        "condense_limit": CONDENSE_LIMIT,
        "shrink_floor": SHRINK_FLOOR,
    }


def _font_info(font: FontFacts, language: str) -> FontInfo:
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
        "undo_redacted_words": words.sentence("undo_redacted_words", language),
        "split_made": words.sentence("split_made", language),
        "split_joined": words.sentence("split_joined", language),
        "split_changed": words.sentence("split_changed", language),
        "split_cant_here": words.sentence("split_cant_here", language),
        "split_redacted": words.sentence("split_redacted", language),
        "reopened": words.sentence("reopened", language),
        "export_left_out": words.sentence("export_left_out", language),
        "page_left_out": words.sentence("page_left_out", language),
        "page_put_back": words.sentence("page_put_back", language),
        "how_to_fix": words.sentence("how_to_fix", language),
        "how_to_fix_touch": words.sentence("how_to_fix_touch", language),
        "how_to_add": words.sentence("how_to_add", language),
        "how_to_add_touch": words.sentence("how_to_add_touch", language),
        "new_text": words.sentence("new_text", language),
        "new_text_moves": words.sentence("new_text_moves", language),
        "new_text_moves_touch": words.sentence("new_text_moves_touch", language),
        "form_field_not_edited": words.sentence("form_field_not_edited", language),
        "empty": words.sentence("empty", language),
        "edit_breaks_signature": words.sentence("edit_breaks_signature", language),
        "email_broken": words.sentence("email_broken", language),
        "back_to": words.sentence("back_to", language),
        "changed_to": words.sentence("changed_to", language),
        "put_back": words.sentence("put_back", language),
        "redacted": words.sentence("redacted", language),
        "already_redacted": words.sentence("already_redacted", language),
        "new_text_added": words.sentence("new_text_added", language),
        "new_text_moved": words.sentence("new_text_moved", language),
        "new_text_taken_out": words.sentence("new_text_taken_out", language),
        "new_text_set_in": words.sentence("new_text_set_in", language),
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
        "find_empty": _counted_in("find_empty", language),
        "find_some_empty": _counted_in("find_some_empty", language),
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
        "join_sentences": words.sentence("join_sentences", language),
        "options": options,
        "approximate": {
            reason: words.sentence(reason, language) for reason in APPROXIMATE_REASONS
        },
        "characters": words.character_names(language),
    }


def _counted_in(key: str, language: str) -> CountedSentence:
    forms = words.forms(key, language)
    return {"one": forms["one"], "other": forms["other"]}


def _notices_in(
    *,
    has_text: bool,
    active_content_dropped: bool,
    plain_xmp_dropped: bool,
    signed: bool,
    language: str,
) -> list[DocumentNoticeInfo]:
    said: list[Message] = []
    if not has_text:
        said.append(Message("no_text"))
    if active_content_dropped:
        said.append(Message("active_content_dropped"))
    if plain_xmp_dropped:
        said.append(Message("plain_xmp_dropped"))
    if signed:
        said.append(Message("signed_file"))
    return [{"type": message.key, **words.said(message, language)} for message in said]
