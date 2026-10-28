"""The file's protection, as its Encrypt dictionary keeps it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import keys_of, pdf_of, values_of

# What a reader reads of the Encrypt dictionary to make the key, as the standard and the
# public-key protection write it; it reads no other entry before it can decrypt.
_KEY_ENTRIES = (
    "Filter",
    "SubFilter",
    "V",
    "R",
    "Length",
    "P",
    "O",
    "U",
    "OE",
    "UE",
    "Perms",
    "EncryptMetadata",
    "Recipients",
    "CF",
    "StmF",
    "StrF",
    "EFF",
)
# What it reads of each crypt filter (each of CF's dictionaries).
_CRYPT_FILTER_ENTRIES = ("Type", "CFM", "AuthEvent", "Length", "Recipients", "EncryptMetadata")


@dataclass(frozen=True, slots=True, eq=False)
class Protection:
    """The file's protection: what a reader reads before it can decrypt anything."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def write_entries_in_place(self) -> None:
        """Write what a reader reads to make the key in place, where it's kept as an object.

        In place: inside the dictionary itself, not as an object of its own. A save may put
        such an object in an object stream, which only the key opens, and it makes the key.
        """
        mu = pymupdf.mupdf
        encrypt = mu.pdf_dict_get(mu.pdf_trailer(pdf_of(self.doc)), mu.PDF_ENUM_NAME_Encrypt)
        _write_in_place(encrypt, _KEY_ENTRIES)
        crypt_filters = mu.pdf_dict_get(encrypt, mu.PDF_ENUM_NAME_CF)
        # Every one of them, as the method's entries (StmF, StrF) may name any.
        _write_in_place(crypt_filters, [mu.pdf_to_name(key) for key in keys_of(crypt_filters)])
        for crypt_filter in values_of(crypt_filters):
            _write_in_place(crypt_filter, _CRYPT_FILTER_ENTRIES)


def _write_in_place(dictionary: pymupdf.mupdf.PdfObj, keys: Sequence[str]) -> None:
    """Put each of `keys` that `dictionary` keeps as an object in it instead, as a copy.

    The copy is of where the reference leads, through any that name another; what the
    object names in turn stays as it is.
    """
    mu = pymupdf.mupdf
    for key in keys:
        value = mu.pdf_dict_gets(dictionary, key)
        if not mu.pdf_is_indirect(value):
            continue
        # Leading nowhere, or round to itself, it reads as null, as a reader takes it.
        held = mu.pdf_resolve_indirect_chain(value)
        # A copy: the object itself stays where it is, for whatever else names it.
        mu.pdf_dict_puts(dictionary, key, mu.pdf_deep_copy_obj(held))
