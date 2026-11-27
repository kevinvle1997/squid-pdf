"""Run with `uv run --with pikepdf==10.17.0`: MuPDF can't write a file whose XMP stays plain."""

from __future__ import annotations

from pathlib import Path

import pikepdf

_OWNER_PASSWORD = "owner"
_LINE = "Quarterly report"
_XMP_TITLE = "Project Halcyon"
_XMP = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{_XMP_TITLE}</rdf:li></rdf:Alt></dc:title>
</rdf:Description></rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>"""
_FILES = {
    "plain_xmp_aes128.pdf": (4, None),
    "plain_xmp_aes256.pdf": (6, None),
    "plain_xmp_shared.pdf": (4, "page"),
    "plain_xmp_pieceinfo.pdf": (4, "pieceinfo"),
}


def _with_plain_xmp(*, shared: str | None) -> pikepdf.Pdf:
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(f"BT /F1 24 Tf 72 700 Td ({_LINE}) Tj ET".encode())
    helvetica = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica))
    pdf.docinfo.Title = _LINE
    xmp = pdf.make_indirect(pdf.make_stream(_XMP.encode()))
    xmp.Type, xmp.Subtype = pikepdf.Name.Metadata, pikepdf.Name.XML
    pdf.Root.Metadata = xmp
    if shared == "page":
        page.obj.Metadata = xmp
    if shared == "pieceinfo":
        pdf.Root.PieceInfo = pikepdf.Dictionary(App=pikepdf.Dictionary(Private=xmp))
    return pdf


def main() -> None:
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
