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

_ACTION_KEYS = ("A", "OpenAction")

# A tag's `A` is its layout, and stays.
_ACTING_KEYS = ("Subtype", "FT", "Title")

_HARMLESS_ACTIONS = ("GoTo", "Named")

_WEB_SCHEMES = ("http:", "https:", "mailto:")

_OUTSIDE_STREAM_KEYS = ("F", "FFilter", "FDecodeParms", "Ref")

_NAME_END = r"(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)"

_ACTIVE_NAMED = re.compile(rf"/(?:{'|'.join(_DROPPED_KEYS + _ACTION_KEYS)}){_NAME_END}")

_ACTIVE_NAMED_IN_STREAM = re.compile(
    rf"/(?:{'|'.join(_DROPPED_KEYS + _ACTION_KEYS + _OUTSIDE_STREAM_KEYS)}){_NAME_END}"
)


@dataclass(frozen=True, slots=True, eq=False)
class ActiveContent:
    doc: pymupdf.Document

    def drop(self) -> bool:
        """Every object, not only those the pages reach: a viewer may read any of them."""
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        dropped = False
        for number in range(1, mu.pdf_xref_len(pdf)):
            is_stream = mu.pdf_obj_num_is_stream(pdf, number)
            named = _ACTIVE_NAMED_IN_STREAM if is_stream else _ACTIVE_NAMED
            if not self._may_name(number, named):
                continue
            held = mu.pdf_resolve_indirect(mu.pdf_new_indirect(pdf, number, 0))
            outside = _OUTSIDE_STREAM_KEYS if is_stream else ()
            dropped |= _dropped_from(held, outside)
            for inner in _inner_dictionaries(held):
                dropped |= _dropped_from(inner, ())
        return dropped

    def _may_name(self, number: int, named: re.Pattern[str]) -> bool:
        """Printed whole, as PyMuPDF cuts text at a NUL; may match too many, never too few."""
        # MuPDF raises on an object it can't print: it's walked, so a failure lets nothing by.
        try:
            printed = self.doc.xref_object(number, compressed=True)
        except MUPDF_ERRORS:
            return True
        return named.search(printed) is not None


def _dropped_from(dictionary: pymupdf.mupdf.PdfObj, outside: tuple[str, ...]) -> bool:
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
    mu = pymupdf.mupdf
    if not mu.pdf_is_dict(action):
        return True
    if not mu.pdf_is_null(mu.pdf_dict_gets(action, "Next")):
        return False
    kind = mu.pdf_to_name(mu.pdf_dict_gets(action, "S"))
    if kind in _HARMLESS_ACTIONS:
        return True
    address = mu.pdf_to_text_string(mu.pdf_dict_gets(action, "URI")).strip().lower()
    return kind == "URI" and address.startswith(_WEB_SCHEMES)


def _inner_dictionaries(held: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    mu = pymupdf.mupdf
    waiting = values_of(held) if mu.pdf_is_dict(held) else [held]
    while waiting:
        node = waiting.pop()
        if mu.pdf_is_indirect(node):
            continue
        if mu.pdf_is_array(node):
            waiting += items_of(node)
            continue
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += values_of(node)
