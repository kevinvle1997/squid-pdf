"""Rebuild the protected files that keep their XMP plain, for the export's tests.

    uv run --with pikepdf==10.17.0 python fixtures/plain_xmp.py

Each asks for its XMP to stay unencrypted (EncryptMetadata false), as a search reads it
without the password. MuPDF writes no such file, so qpdf does, through pikepdf.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

_OWNER_PASSWORD = "owner"  # an owner password alone, so anyone opens it, squidpdf included
_LINE = "Quarterly report"  # the page's one line, and the Info's title
_XMP_TITLE = "Project Halcyon"  # the XMP's title: a word nothing else in the file holds
_XMP = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{_XMP_TITLE}</rdf:li></rdf:Alt></dc:title>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>"""
# Each file: the revision of the standard protection it uses, AES-128 (R4) or AES-256 (R6),
# and what else names the catalog's XMP, which leaves it in the file if kept: its page's
# Metadata, or the private data an application keeps in the catalog (PieceInfo).
_FILES = {
    "plain_xmp_aes128.pdf": (4, None),
    "plain_xmp_aes256.pdf": (6, None),
    "plain_xmp_shared.pdf": (4, "page"),
    "plain_xmp_pieceinfo.pdf": (4, "pieceinfo"),
}


def _with_plain_xmp(*, shared: str | None) -> pikepdf.Pdf:
    """One page of `_LINE` in Helvetica, its Info titled `_LINE`, its XMP `_XMP_TITLE`."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(f"BT /F1 24 Tf 72 700 Td ({_LINE}) Tj ET".encode())
    # An object of its own, as most writers keep a font.
    helvetica = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica))
    pdf.docinfo.Title = _LINE
    # An object of its own, so a page naming it names the same one.
    xmp = pdf.make_indirect(pdf.make_stream(_XMP.encode()))
    xmp.Type, xmp.Subtype = pikepdf.Name.Metadata, pikepdf.Name.XML
    pdf.Root.Metadata = xmp
    if shared == "page":
        page.obj.Metadata = xmp
    if shared == "pieceinfo":
        pdf.Root.PieceInfo = pikepdf.Dictionary(App=pikepdf.Dictionary(Private=xmp))
    return pdf


def main() -> None:
    """Write each file beside this script."""
    for name, (revision, shared) in _FILES.items():
        # metadata=False: the XMP is written as it is, the rest encrypted.
        protection = pikepdf.Encryption(
            owner=_OWNER_PASSWORD, user="", R=revision, aes=True, metadata=False
        )
        path = Path(__file__).with_name(name)
        # fix_metadata_version=False: the XMP stays as written, in the object it was made in.
        pdf = _with_plain_xmp(shared=shared)
        pdf.save(path, encryption=protection, fix_metadata_version=False)


if __name__ == "__main__":
    main()
