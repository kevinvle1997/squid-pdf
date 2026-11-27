from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.fields import FIELD_VALUE, form_fields
from squidpdf.core.pdf.lowlevel.objects import root_of

_SIGNATURE_CHECKS = ("Perms", "DSS")
_SIGNING_PERMS = ("DocMDP", "UR3")


@dataclass(frozen=True, slots=True, eq=False)
class Signatures:
    doc: pymupdf.Document

    def is_signed(self) -> bool:
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        kept = [mu.pdf_dict_gets(root, key) for key in _SIGNATURE_CHECKS]
        return bool(self._signed_fields(root)) or not all(map(mu.pdf_is_null, kept))

    def holds_signature(self) -> bool:
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        perms = mu.pdf_dict_gets(root, "Perms")
        signing = [mu.pdf_dict_gets(perms, key) for key in _SIGNING_PERMS]
        return bool(self._signed_fields(root)) or any(map(mu.pdf_is_dict, signing))

    def drop(self) -> None:
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        for field in self._signed_fields(root):
            mu.pdf_dict_dels(field, FIELD_VALUE)
        for key in _SIGNATURE_CHECKS:
            mu.pdf_dict_dels(root, key)

    def _signed_fields(self, root: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
        mu = pymupdf.mupdf
        return [
            field
            for field in form_fields(self.doc, root)
            if mu.pdf_is_dict(mu.pdf_dict_gets(field, FIELD_VALUE))
        ]
