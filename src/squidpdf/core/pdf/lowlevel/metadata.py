from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pymupdf

from squidpdf.core.pdf.lowlevel.objects import (
    bytes_of,
    metadata_stream,
    pdf_of,
    put_stream,
    put_text,
)
from squidpdf.core.pdf.xmp import set_changed_by, xml_of, xml_written

_INFO_DATE_FORMAT = "D:%Y%m%d%H%M%SZ"


@dataclass(frozen=True, slots=True, eq=False)
class Metadata:
    doc: pymupdf.Document

    def mark_changed_by(self, producer: str, when: datetime) -> None:
        self._mark_info(producer, when)
        self._mark_xmp(producer, when)

    def _mark_info(self, producer: str, when: datetime) -> None:
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        info = mu.pdf_dict_get(mu.pdf_trailer(pdf), mu.PDF_ENUM_NAME_Info)
        if not mu.pdf_is_dict(info):
            info = mu.pdf_add_new_dict(pdf, 2)
            # The trailer again: adding an object moves it, and a put on the old one is lost.
            mu.pdf_dict_put(mu.pdf_trailer(pdf), mu.PDF_ENUM_NAME_Info, info)
        mod_date = when.astimezone(UTC).strftime(_INFO_DATE_FORMAT)
        put_text(info, "Producer", producer)
        put_text(info, "ModDate", mod_date)

    def _mark_xmp(self, producer: str, when: datetime) -> None:
        stream = metadata_stream(self.doc)
        if stream is None:
            return
        dom = xml_of(bytes_of(stream))
        if dom is None:
            return
        if not set_changed_by(dom, producer, when):
            return
        put_stream(self.doc, stream, xml_written(dom))
