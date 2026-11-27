from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import keys_of, pdf_of, values_of

# A reader reads no other entry of Encrypt before it can decrypt.
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
_CRYPT_FILTER_ENTRIES = ("Type", "CFM", "AuthEvent", "Length", "Recipients", "EncryptMetadata")


@dataclass(frozen=True, slots=True, eq=False)
class Protection:
    doc: pymupdf.Document

    def write_entries_in_place(self) -> None:
        """A save may put it in an object stream, which only the key it makes opens."""
        mu = pymupdf.mupdf
        encrypt = mu.pdf_dict_get(mu.pdf_trailer(pdf_of(self.doc)), mu.PDF_ENUM_NAME_Encrypt)
        _write_in_place(encrypt, _KEY_ENTRIES)
        crypt_filters = mu.pdf_dict_get(encrypt, mu.PDF_ENUM_NAME_CF)
        _write_in_place(crypt_filters, [mu.pdf_to_name(key) for key in keys_of(crypt_filters)])
        for crypt_filter in values_of(crypt_filters):
            _write_in_place(crypt_filter, _CRYPT_FILTER_ENTRIES)


def _write_in_place(dictionary: pymupdf.mupdf.PdfObj, keys: Sequence[str]) -> None:
    mu = pymupdf.mupdf
    for key in keys:
        value = mu.pdf_dict_gets(dictionary, key)
        if not mu.pdf_is_indirect(value):
            continue
        # Leading nowhere, or round to itself, it reads as null, as a reader takes it.
        held = mu.pdf_resolve_indirect_chain(value)
        mu.pdf_dict_puts(dictionary, key, mu.pdf_deep_copy_obj(held))
