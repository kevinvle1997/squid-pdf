from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.active_content import ActiveContent
from squidpdf.core.pdf.lowlevel.additions import PageAdditions
from squidpdf.core.pdf.lowlevel.document_hidden_copies import DocumentHiddenCopies
from squidpdf.core.pdf.lowlevel.fonts import FontRecords
from squidpdf.core.pdf.lowlevel.metadata import Metadata
from squidpdf.core.pdf.lowlevel.page_glyphs import PageGlyphs
from squidpdf.core.pdf.lowlevel.page_hidden_copies import PageHiddenCopies
from squidpdf.core.pdf.lowlevel.protection import Protection
from squidpdf.core.pdf.lowlevel.redaction import PageRedaction
from squidpdf.core.pdf.lowlevel.signatures import Signatures


@dataclass(frozen=True, slots=True, eq=False)
class PdfFile:
    fonts: FontRecords
    glyphs: PageGlyphs
    redaction: PageRedaction
    additions: PageAdditions
    page_hidden_copies: PageHiddenCopies
    document_hidden_copies: DocumentHiddenCopies
    signatures: Signatures
    active_content: ActiveContent
    metadata: Metadata
    protection: Protection


def pdf_file(doc: pymupdf.Document) -> PdfFile:
    return PdfFile(
        FontRecords(doc),
        glyphs=PageGlyphs(doc),
        redaction=PageRedaction(doc),
        additions=PageAdditions(doc),
        page_hidden_copies=PageHiddenCopies(doc),
        document_hidden_copies=DocumentHiddenCopies(doc),
        signatures=Signatures(doc),
        active_content=ActiveContent(doc),
        metadata=Metadata(doc),
        protection=Protection(doc),
    )
