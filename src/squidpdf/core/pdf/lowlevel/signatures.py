"""A signed file's signatures, read and dropped."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.fields import FIELD_VALUE, form_fields
from squidpdf.core.pdf.lowlevel.objects import root_of

# What a signed file's catalog keeps only for its signatures, so it goes with them.
_SIGNATURE_CHECKS = ("Perms", "DSS")
# The permissions (Perms) that are signatures: one certifying the file, one granting rights.
_SIGNING_PERMS = ("DocMDP", "UR3")


@dataclass(frozen=True, slots=True, eq=False)
class Signatures:
    """A signed file's signatures, and what its catalog keeps only for them."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def is_signed(self) -> bool:
        """Whether the document holds a signature, or what a signed file keeps for one."""
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        kept = [mu.pdf_dict_gets(root, key) for key in _SIGNATURE_CHECKS]
        return bool(self._signed_fields(root)) or not all(map(mu.pdf_is_null, kept))

    def holds_signature(self) -> bool:
        """Whether a form field's value, or one of the permissions (Perms), is a signature."""
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        perms = mu.pdf_dict_gets(root, "Perms")
        signing = [mu.pdf_dict_gets(perms, key) for key in _SIGNING_PERMS]
        return bool(self._signed_fields(root)) or any(map(mu.pdf_is_dict, signing))

    def drop(self) -> None:
        """Delete every signature, and what the file keeps only to check one."""
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        for field in self._signed_fields(root):
            mu.pdf_dict_dels(field, FIELD_VALUE)
        for key in _SIGNATURE_CHECKS:
            mu.pdf_dict_dels(root, key)

    def _signed_fields(self, root: pymupdf.mupdf.PdfObj) -> list[pymupdf.mupdf.PdfObj]:
        """Each signed form field: one whose value, a signature, is a dictionary."""
        mu = pymupdf.mupdf
        return [
            field
            for field in form_fields(self.doc, root)
            if mu.pdf_is_dict(mu.pdf_dict_gets(field, FIELD_VALUE))
        ]
