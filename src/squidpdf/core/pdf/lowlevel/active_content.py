"""What acts on its own or reaches outside the file (active content), found and deleted."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.errors import MUPDF_ERRORS
from squidpdf.core.pdf.lowlevel.objects import (
    items_of,
    keys_of,
    pdf_of,
    values_of,
)

# What a file keeps only to act on its own or hold files: event actions (AA), named scripts and
# files, file bytes (EF) and systems (FS), associated files (AF), 3D scripts, movies, XFA.
_DROPPED_KEYS = (
    "AA",
    "JavaScript",
    "EmbeddedFiles",
    "EF",
    "FS",
    "AF",
    "OnInstantiate",
    "Movie",
    "XFA",
    "NeedsRendering",
)

_ACTION_KEYS = ("A", "OpenAction")  # an action: kept only when `_is_harmless`

# What only an annotation (Subtype), a form field (FT) or a bookmark (Title) has: where `A` is
# an action. A tag's `A` is its layout, and stays.
_ACTING_KEYS = ("Subtype", "FT", "Title")

_HARMLESS_ACTIONS = ("GoTo", "Named")  # a move within the file

_WEB_SCHEMES = ("http:", "https:", "mailto:")  # a link to the web, which the reader follows

# A stream's bytes, or a drawing, kept in another file, and how they'd be read.
_OUTSIDE_STREAM_KEYS = ("F", "FFilter", "FDecodeParms", "Ref")

# What ends a name as MuPDF prints one: a delimiter, a space, or the end.
_NAME_END = r"(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)"

# Where a printed object names a key `_dropped_from` may delete from any dictionary; a string
# holding the name matches too, which costs only a walk.
_ACTIVE_NAMED = re.compile(rf"/(?:{'|'.join(_DROPPED_KEYS + _ACTION_KEYS)}){_NAME_END}")

# The same, or a key only a stream's own dictionary loses.
_ACTIVE_NAMED_IN_STREAM = re.compile(
    rf"/(?:{'|'.join(_DROPPED_KEYS + _ACTION_KEYS + _OUTSIDE_STREAM_KEYS)}){_NAME_END}"
)


@dataclass(frozen=True, slots=True, eq=False)
class ActiveContent:
    """What acts on its own or reaches outside the file, found and deleted."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def drop(self) -> bool:
        """Delete what acts on its own or reaches outside the file; whether there was any.

        Every object is read, not only those the pages reach: a viewer may read any of them.
        """
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        dropped = False
        for number in range(1, mu.pdf_xref_len(pdf)):
            is_stream = mu.pdf_obj_num_is_stream(pdf, number)
            named = _ACTIVE_NAMED_IN_STREAM if is_stream else _ACTIVE_NAMED
            # Naming no key it could delete, as most objects: nothing to walk.
            if not self._may_name(number, named):
                continue
            held = mu.pdf_resolve_indirect(mu.pdf_new_indirect(pdf, number, 0))
            # A stream's own dictionary may say its bytes are in another file.
            outside = _OUTSIDE_STREAM_KEYS if is_stream else ()
            dropped |= _dropped_from(held, outside)
            for inner in _inner_dictionaries(held):
                dropped |= _dropped_from(inner, ())
        return dropped

    def _may_name(self, number: int, named: re.Pattern[str]) -> bool:
        """Whether object `number`, printed whole, holds a name `named` finds.

        Whole: PyMuPDF cuts the text at a NUL, and MuPDF prints none: a name decoded, an odd
        byte as #xx (`/J#61vaScript` as `/JavaScript`), a string escaped or in hex. A string's
        backslash escape may read as a letter: a match too many, never one missed.
        """
        # MuPDF raises on an object it can't print: it's walked, so a failure lets nothing by.
        try:
            printed = self.doc.xref_object(number, compressed=True)
        except MUPDF_ERRORS:
            return True
        return named.search(printed) is not None


def _dropped_from(dictionary: pymupdf.mupdf.PdfObj, outside: tuple[str, ...]) -> bool:
    """Delete `dictionary`'s active entries, and those in `outside`; whether it had any."""
    mu = pymupdf.mupdf
    keys = [mu.pdf_to_name(key) for key in keys_of(dictionary)]
    acts = "OpenAction" in keys or any(key in keys for key in _ACTING_KEYS)
    dropped = [
        key
        for key in keys
        if key in _DROPPED_KEYS
        or key in outside
        or (
            acts and key in _ACTION_KEYS and not _is_harmless(mu.pdf_dict_gets(dictionary, key))
        )
    ]
    for key in dropped:
        mu.pdf_dict_dels(dictionary, key)
    return bool(dropped)


def _is_harmless(action: pymupdf.mupdf.PdfObj) -> bool:
    """Whether an action only moves within the file or links to the web, and then stops."""
    mu = pymupdf.mupdf
    # A destination written alone, as a file's open action may be: a place in the file.
    if not mu.pdf_is_dict(action):
        return True
    # Another action after it, which may be anything.
    if not mu.pdf_is_null(mu.pdf_dict_gets(action, "Next")):
        return False
    kind = mu.pdf_to_name(mu.pdf_dict_gets(action, "S"))
    # A move within the file.
    if kind in _HARMLESS_ACTIONS:
        return True
    # A link, kept only to the web: `javascript:` and a file's own path are no web page.
    address = mu.pdf_to_text_string(mu.pdf_dict_gets(action, "URI")).strip().lower()
    return kind == "URI" and address.startswith(_WEB_SCHEMES)


def _inner_dictionaries(held: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    """Each dictionary written inside `held`, however deep; not the objects it points at.

    Those are objects of their own, each read in its turn. Read after the caller has
    dropped what it would, so nothing dropped is read.
    """
    mu = pymupdf.mupdf
    waiting = values_of(held) if mu.pdf_is_dict(held) else [held]
    while waiting:
        node = waiting.pop()
        # An object of its own: read in its turn.
        if mu.pdf_is_indirect(node):
            continue
        # A list: each of its items is read.
        if mu.pdf_is_array(node):
            waiting += items_of(node)
            continue
        # Anything else that isn't a dictionary (a number, a name) holds none.
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += values_of(node)
