"""Every sentence the app says about a document, in one place, each under a key.

The sentences are English, and `ENGLISH_SENTENCES` files each under the key a
`core.message.Message` names it by. Below the API nothing says a sentence
outright: it names one by its key, with the facts that fill it, and the edge
calls `render` in the reader's language. The CLI renders in English.

Another language is a dict with the same keys and the same placeholders, added
to `CATALOGS`; a key it lacks is said in English. None ships yet.

Placeholders are bare `{name}`, never a format spec, so the browser can fill
them too: `{chars}` is characters joined with " or ", `{letters}` characters
joined with spaces, and a number with a fraction, like `{delta_pt}` in points,
is written to one decimal place. A character that draws nothing a person could
see, such as a narrow no-break space, is written as its Unicode name.

A key is never renamed: the browser can branch on it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from squidpdf.core.message import Message, Param

# What's wrong with a replacement.
# `{font}` is the face that draws the line instead.
MISSING = "no {chars} in this font, so the line is drawn in {font}"
WILL_LEAVE_OUT = "{letters} will be left out: no font we have can draw them"
CHOSEN_UNAVAILABLE = "{chosen} can't be used here, so this is drawn in {font}"
NOT_OFFERED = "too far to fit without looking different, so it's left long"
TOO_LONG = "{delta_pt} pt too long"
# A font the file doesn't let us use. `{font}` is the face that draws instead.
STAND_IN = "Edits here use {font}, which may be a different width from the original."
# The same, when that face's letters are exactly as wide as the original's.
STAND_IN_SAME_WIDTHS = (
    "Edits here use {font}, whose letters are the same width as the original's."
)

# Why an edit was left out.
NO_SPAN = "This edit points at text that isn't in this document."
NO_PAGE = "This edit points at a page that isn't in this document."

# Why a font's own copy can't be used, so a similar font stands in. One per font.
FONT_NOT_IN_FILE = "This font isn't stored in the file, so a similar font stands in for it."
FONT_UNREADABLE = (
    "This font is stored in the file but can't be read, so a similar font stands in for it."
)
FONT_NO_LETTER_LIST = (
    "This font is stored in the file without saying which shape is which letter,"
    " so a similar font stands in for it."
)
FONT_LACKS_LETTERS = (
    "The file's copy of this font can't draw every letter here,"
    " so a similar font stands in for it."
)
FONT_CANT_WRITE = (
    "This font is stored in the file in a way we can't write new text with yet,"
    " so a similar font stands in for it."
)

# An edit that went in, but not quite as asked. `{letters}` is space-separated.
FONT_NOT_ADDED = (
    "The file's own font couldn't be used for this edit, so a similar font drew it."
)
LEFT_OUT = "Left out {letters}: no font we have can draw them."
# `{font}` is a face we ship.
FACE_NOT_TRIMMED = (
    "{font} went into the file whole, not only the letters used,"
    " so the file is larger than it needs to be."
)
REDACTION_UNDONE = "Editing this text undid its redaction."

# Asked before the user edits text they redacted: yes goes ahead, no keeps the redaction.
UNDO_REDACTION = "This text is redacted. Editing it undoes the redaction. Edit it anyway?"

# The ways out of an overflow: what each is called, and what it does.
SHRINK_LABEL = "Make it slightly smaller"
SHRINK_DETAIL = "Reduces the type size just enough to fit."
CONDENSE_LABEL = "Tighten the letters"
CONDENSE_DETAIL = "Squeezes the spacing by a few percent, which isn't noticeable at this size."
AS_IS_LABEL = "Leave it long"
AS_IS_DETAIL = "Runs {delta_pt} pt past where the original ended."

# A Problem's `detail`, shown to the user verbatim. Limits are placeholders so
# the sentence can't drift from the number the server enforces.
NOT_A_PDF = "This isn't a PDF."
TOO_LARGE = "This file is over {mb} MB."
TOO_MANY_PAGES = "This PDF has more than {pages} pages."
ENCRYPTED = "This PDF is password-protected. Open it with the password and save a copy first."
DAMAGED = "This PDF is damaged and can't be opened."
NOT_FOUND = "Nothing here. It may have expired."  # never shown: the browser re-uploads
NO_SUCH_PAGE = "This document doesn't have that page."
REDACTION_CONFLICT = "This text has a redaction. Undo it to edit."
BAD_REFERENCE = "A redaction points at text that isn't in this document: {span_id}."
REDACTION_FAILED = "Couldn't remove “{text}” on page {page}, so nothing was downloaded."
FONT_MISMATCH = "This isn't the font the document uses. Its letters are a different width."
INVALID_REQUEST = "Something went wrong sending your changes. Reload the page and try again."
RATE_LIMITED = "Too many files at once. Try again in a minute."
SUPPORT_EMAIL = "support@example.com"  # a placeholder until there's a real inbox
TOO_SLOW = (
    "This took too long, so we stopped. Try again,"
    f" and if it keeps happening, email {SUPPORT_EMAIL}."
)
TOO_HEAVY = (
    "This PDF needs more memory than we can give it."
    f" If it keeps happening, email {SUPPORT_EMAIL}."
)
TOO_MANY_EDITS = (
    "That's more than {edits} changes at once. Download what you have and go on from there."
)
TEXT_TOO_LONG = "New text can be up to {chars} characters."
REQUEST_TOO_LARGE = (
    "That's too much to send at once. Download what you have and go on from there."
)
SERVER_ERROR = "Something went wrong on our side. Try again."

# A notice on an upload that worked, but may not be what the user expected.
NO_TEXT = "This PDF has no text we can edit. It may be a scan or a photo of the page."

# Every sentence above under the key a Message names it by. A Problem's key is its type.
ENGLISH_SENTENCES: dict[str, str] = {
    "missing": MISSING,
    "will_leave_out": WILL_LEAVE_OUT,
    "chosen_unavailable": CHOSEN_UNAVAILABLE,
    "not_offered": NOT_OFFERED,
    "too_long": TOO_LONG,
    "stand_in": STAND_IN,
    "stand_in_same_widths": STAND_IN_SAME_WIDTHS,
    "no_span": NO_SPAN,
    "no_page": NO_PAGE,
    "font_not_in_file": FONT_NOT_IN_FILE,
    "font_unreadable": FONT_UNREADABLE,
    "font_no_letter_list": FONT_NO_LETTER_LIST,
    "font_lacks_letters": FONT_LACKS_LETTERS,
    "font_cant_write": FONT_CANT_WRITE,
    "font_not_added": FONT_NOT_ADDED,
    "left_out": LEFT_OUT,
    "face_not_trimmed": FACE_NOT_TRIMMED,
    "redaction_undone": REDACTION_UNDONE,
    "undo_redaction": UNDO_REDACTION,
    "shrink_label": SHRINK_LABEL,
    "shrink_detail": SHRINK_DETAIL,
    "condense_label": CONDENSE_LABEL,
    "condense_detail": CONDENSE_DETAIL,
    "as_is_label": AS_IS_LABEL,
    "as_is_detail": AS_IS_DETAIL,
    "not_a_pdf": NOT_A_PDF,
    "too_large": TOO_LARGE,
    "too_many_pages": TOO_MANY_PAGES,
    "encrypted": ENCRYPTED,
    "damaged": DAMAGED,
    "not_found": NOT_FOUND,
    "no_such_page": NO_SUCH_PAGE,
    "redaction_conflict": REDACTION_CONFLICT,
    "bad_reference": BAD_REFERENCE,
    "redaction_failed": REDACTION_FAILED,
    "font_mismatch": FONT_MISMATCH,
    "invalid_request": INVALID_REQUEST,
    "rate_limited": RATE_LIMITED,
    "too_slow": TOO_SLOW,
    "too_heavy": TOO_HEAVY,
    "too_many_edits": TOO_MANY_EDITS,
    "text_too_long": TEXT_TOO_LONG,
    "request_too_large": REQUEST_TOO_LARGE,
    "server_error": SERVER_ERROR,
    "no_text": NO_TEXT,
    # Not sentences: what joins a list's items, and what joins several messages in one line.
    "join_chars": " or ",
    "join_letters": " ",
    "join_parts": "; ",
}

ENGLISH = "en"
# Every language we can answer in, by its lower-case tag; English is the fallback.
CATALOGS: dict[str, Mapping[str, str]] = {ENGLISH: ENGLISH_SENTENCES}

# Each way out of an overflow, by the name an edit's `strategy` uses: its sentences' keys.
OPTION_KEYS: dict[str, dict[str, str]] = {
    "shrink": {"label": "shrink_label", "detail": "shrink_detail"},
    "condense": {"label": "condense_label", "detail": "condense_detail"},
    "as-is": {"label": "as_is_label", "detail": "as_is_detail"},
}


def sentence(key: str, language: str = ENGLISH, default: str | None = None) -> str:
    """The sentence under `key` in `language`, placeholders unfilled.

    A key the language lacks is said in English, then as `default`. Raises
    KeyError when there's none of those: a key no catalog has is a bug.
    """
    in_english = ENGLISH_SENTENCES.get(key, default)
    found = CATALOGS.get(language, ENGLISH_SENTENCES).get(key, in_english)
    if found is None:
        raise KeyError(key)
    return found


def catalog(language: str) -> dict[str, str]:
    """Every sentence as `language` says it, English where it has none."""
    return {key: sentence(key, language) for key in ENGLISH_SENTENCES}


def render(message: Message, language: str = ENGLISH) -> str:
    """`message` as a person reads it in `language`: its sentence, placeholders filled."""
    return fill(sentence(message.key, language), message.params, language)


def render_all(messages: Iterable[Message], language: str = ENGLISH) -> str | None:
    """Several messages as one line, in the order given; None when there are none."""
    joiner = sentence("join_parts", language)
    return joiner.join(render(message, language) for message in messages) or None


def fill(template: str, params: Mapping[str, Param], language: str = ENGLISH) -> str:
    """`template` with each placeholder's fact written out as a person reads it."""
    written = {name: _written(name, value, language) for name, value in params.items()}
    return template.format_map(written)


def _written(name: str, value: Param, language: str) -> str:
    """One fact as it reads in a sentence: a list joined, a fraction to one decimal place."""
    if isinstance(value, list):
        return sentence(f"join_{name}", language).join(_visible(item) for item in value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _visible(character: str) -> str:
    """A character as a person can see it: itself, or its name when it draws nothing.

    Names are Unicode's, lower case, e.g. "narrow no-break space"; one without a
    name, such as a control character, is its code point, e.g. "U+0009".
    """
    shows = len(character) != 1 or (character.isprintable() and not character.isspace())
    if shows:
        return character
    try:
        return unicodedata.name(character).lower()
    except ValueError:  # control characters and unassigned code points have no name
        return f"U+{ord(character):04X}"
