"""The file's metadata, its Info and its XMP, marked with the program that last changed it."""

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

_INFO_DATE_FORMAT = "D:%Y%m%d%H%M%SZ"  # a date as the Info writes one, here in UTC (Z)


@dataclass(frozen=True, slots=True, eq=False)
class Metadata:
    """The file's metadata: its Info, and its XMP where it has one."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def mark_changed_by(self, producer: str, when: datetime) -> None:
        """Say in the Info, and the XMP where it can, that `producer` changed it, at `when`."""
        self._mark_info(producer, when)
        self._mark_xmp(producer, when)

    def _mark_info(self, producer: str, when: datetime) -> None:
        """Set the Info's Producer and ModDate, in a new Info when the file has none to set."""
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        info = mu.pdf_dict_get(mu.pdf_trailer(pdf), mu.PDF_ENUM_NAME_Info)
        # None, one that leads nowhere, or one that isn't a dictionary: a new one in its place.
        if not mu.pdf_is_dict(info):
            info = mu.pdf_add_new_dict(pdf, 2)  # room for its two entries
            # The trailer again: adding an object moves it, and a put on the old one is lost.
            mu.pdf_dict_put(mu.pdf_trailer(pdf), mu.PDF_ENUM_NAME_Info, info)
        mod_date = when.astimezone(UTC).strftime(_INFO_DATE_FORMAT)
        put_text(info, "Producer", producer)
        put_text(info, "ModDate", mod_date)

    def _mark_xmp(self, producer: str, when: datetime) -> None:
        """Say the same in the XMP's producer and dates of a change, those it holds."""
        stream = metadata_stream(self.doc)
        if stream is None:
            return
        dom = xml_of(bytes_of(stream))
        # Not XML: it can't be written back as it came, so it stays as the file wrote it.
        if dom is None:
            return
        # None of them in it: it gains none, so nothing to write.
        if not set_changed_by(dom, producer, when):
            return
        put_stream(self.doc, stream, xml_written(dom))
