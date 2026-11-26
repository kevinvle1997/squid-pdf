"""An open PDF as MuPDF's low-level API sees it: one part per concern, over one document."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from squidpdf.core.pdf.lowlevel.active_content import ActiveContent
from squidpdf.core.pdf.lowlevel.additions import PageAdditions
from squidpdf.core.pdf.lowlevel.document_hidden_copies import DocumentHiddenCopies
from squidpdf.core.pdf.lowlevel.fonts import FontRecords
from squidpdf.core.pdf.lowlevel.letters_read import LettersRead
from squidpdf.core.pdf.lowlevel.metadata import Metadata
from squidpdf.core.pdf.lowlevel.page_glyphs import PageGlyphs
from squidpdf.core.pdf.lowlevel.page_hidden_copies import PageHiddenCopies
from squidpdf.core.pdf.lowlevel.protection import Protection
from squidpdf.core.pdf.lowlevel.redaction import PageRedaction
from squidpdf.core.pdf.lowlevel.signatures import Signatures


@dataclass(frozen=True, slots=True, eq=False)
class PdfFile:
    """An open PDF's low-level calls, a part per concern. Made by `pdf_file`."""

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
    # Each page's letters as last read, shared: every part that writes forgets what it changes.
    letters_read: LettersRead


def pdf_file(doc: pymupdf.Document) -> PdfFile:
    """The low-level calls over `doc`, open already: whoever opened it closes it."""
    letters_read = LettersRead()
    return PdfFile(
        FontRecords(doc),
        glyphs=PageGlyphs(doc),
        redaction=PageRedaction(doc, letters_read),
        additions=PageAdditions(doc, letters_read),
        page_hidden_copies=PageHiddenCopies(doc, letters_read),
        document_hidden_copies=DocumentHiddenCopies(doc, letters_read),
        signatures=Signatures(doc, letters_read),
        active_content=ActiveContent(doc, letters_read),
        metadata=Metadata(doc, letters_read),
        protection=Protection(doc, letters_read),
        letters_read=letters_read,
    )
