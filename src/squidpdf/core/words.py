"""Every sentence the app says about a document, in one place.

Pure data. The CLI reads it, the API sends the fit wording to the browser, and
the browser writes only its own chrome. Placeholders are bare `{name}`, never a
format spec, so the browser can fill them too: `{chars}` is the missing
characters joined with " or ", `{delta_pt}` is points to one decimal place.
"""

from __future__ import annotations

# What's wrong with a replacement.
MISSING = "no {chars} in this font"
TOO_LONG = "{delta_pt} pt too long"

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

# The ways out of an overflow, keyed by the name an edit's `strategy` uses.
OPTIONS = {
    "shrink": {
        "label": "Make it slightly smaller",
        "detail": "Reduces the type size just enough to fit.",
    },
    "condense": {
        "label": "Tighten the letters",
        "detail": "Squeezes the spacing by a few percent, which isn't noticeable at this size.",
    },
    "as-is": {
        "label": "Leave it long",
        "detail": "Runs {delta_pt} pt past where the original ended.",
    },
}

# An ApiError's `detail`, shown to the user verbatim. Limits are placeholders so
# the sentence can't drift from the number the server enforces.
NOT_A_PDF = "This isn't a PDF."
TOO_LARGE = "This file is over {mb} MB."
TOO_MANY_PAGES = "This PDF has more than {pages} pages."
ENCRYPTED = "This PDF is password-protected. Open it with the password and save a copy first."
DAMAGED = "This PDF is damaged and can't be opened."
NOT_FOUND = "Nothing here. It may have expired."  # never shown: the browser re-uploads
REDACTION_CONFLICT = "This text has a redaction. Undo it to edit."
BAD_REFERENCE = "A redaction points at text that isn't in this document: {span_id}."
REDACTION_FAILED = "Couldn't remove “{text}” on page {page}, so nothing was downloaded."
FONT_MISMATCH = "This isn't the font the document uses. Its letters are a different width."
INVALID_REQUEST = "Something in the request isn't right ({reason})."
RATE_LIMITED = "Too many files at once. Try again in a minute."
SERVER_ERROR = "Something went wrong on our side. Try again."
