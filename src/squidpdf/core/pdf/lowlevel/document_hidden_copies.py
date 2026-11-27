from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from itertools import chain
from typing import Literal, assert_never

import pymupdf

from squidpdf.core.pdf.lowlevel.drawn_strings import strings_drawn_by
from squidpdf.core.pdf.lowlevel.fields import (
    FIELD_VALUE,
    each_annotation,
    field_line,
    form_fields,
    is_widget,
)
from squidpdf.core.pdf.lowlevel.objects import (
    HIDDEN_COPY_KEYS,
    PDF_NULL,
    bytes_of,
    each_reached,
    items_of,
    keys_of,
    pdf_of,
    put_stream,
    put_text,
    root_of,
    text_of,
    values_of,
)
from squidpdf.core.pdf.xmp import readings_of, xml_of, xml_strings, xml_written
from squidpdf.core.types import CopyPlace, HiddenCopy

_INFO_DATES = ("CreationDate", "ModDate")

_INFO_TITLE = "Title"

_BOOKMARK_TITLE = "Title"

_NEXT_BOOKMARKS = ("Next", "First")

_TAG_KIDS = ("K",)

_FIELD_RESET = "DV"

_FIELD_FORMATTED = "RV"

_FIELD_OPTIONS = "Opt"

_FIELD_DESCRIPTION = "TU"

_FIELD_SEED_VALUES = "SV"

_ANNOTATION_WORDS = "Contents"

_COMMENT_LABELS = ("T", "Subj")

_FORMATTED_WORDS = "RC"

_FIELD_CHOSEN = "I"

_APPEARANCE = "AP"

_CAPTIONS = ("CA", "RC", "AC")

type _Writing = Literal["text", "required_text", "whole", "field_value", "options", "drawing"]

_FIELD_SHOWN: tuple[_Writing, ...] = ("field_value", "options")


@dataclass(frozen=True, slots=True, eq=False)
class DocumentHiddenCopies:
    doc: pymupdf.Document

    def read(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        found = [
            HiddenCopy(entry.place, text)
            for entry in self._document_entries()
            for text in self._texts_of(entry, holding)
        ]
        found += [HiddenCopy("metadata", text) for text in self._metadata_texts()]
        return [hidden_copy for hidden_copy in found if holding(hidden_copy.text)]

    def rewrite(self, rewritten: Callable[[str], str]) -> None:
        mu = pymupdf.mupdf
        holding = partial(_is_changed_by, rewritten)
        # Read first: a key deleted mid-walk would move the ones after it.
        entries = list(self._document_entries())
        drawings = [entry for entry in entries if entry.writing == "drawing"]
        drawing_a_word = [drawing for drawing in drawings if self._texts_of(drawing, holding)]
        changed = [
            entry
            for entry in entries
            if entry.writing != "drawing" and self._rewrite_entry(entry, rewritten)
        ]
        redrawn = self._redrawn(changed, drawing_a_word)
        for page, on_page in redrawn.items():
            self._draw_again(page, on_page)
        drawn_again = {number for on_page in redrawn.values() for number in on_page}
        for drawing in drawings:
            if drawing in drawing_a_word or mu.pdf_to_num(drawing.holder) in drawn_again:
                self._rewrite_entry(drawing, rewritten)
        self._rewrite_metadata(rewritten)

    def _document_entries(self) -> Iterator[_Entry]:
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        info = mu.pdf_dict_get(mu.pdf_trailer(pdf_of(self.doc)), mu.PDF_ENUM_NAME_Info)
        for key in map(mu.pdf_to_name, keys_of(info)):
            if key in _INFO_DATES:
                continue
            yield _Entry("title" if key == _INFO_TITLE else "metadata", info, key, "text")
        outlines = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_Outlines)
        for bookmark in each_reached(
            mu.pdf_dict_get(outlines, mu.PDF_ENUM_NAME_First), _NEXT_BOOKMARKS
        ):
            yield _Entry("bookmarks", bookmark, _BOOKMARK_TITLE, "required_text")
        yield from self._annotation_entries()
        for field in form_fields(self.doc, root):
            yield _Entry("form_fields", field, FIELD_VALUE, "field_value")
            yield _Entry("form_fields", field, _FIELD_RESET, "text")
            yield _Entry("form_fields", field, _FIELD_FORMATTED, "whole")
            yield _Entry("form_fields", field, _FIELD_OPTIONS, "options")
            yield _Entry("form_fields", field, _FIELD_DESCRIPTION, "text")
            yield _Entry("form_fields", field, _FIELD_SEED_VALUES, "whole")
        tags = mu.pdf_dict_get(root, mu.PDF_ENUM_NAME_StructTreeRoot)
        for element in each_reached(mu.pdf_dict_get(tags, mu.PDF_ENUM_NAME_K), _TAG_KIDS):
            for key in HIDDEN_COPY_KEYS:
                yield _Entry("screen_reader_text", element, key, "text")

    def _annotation_entries(self) -> Iterator[_Entry]:
        for page, annotation in each_annotation(self.doc):
            if is_widget(annotation):
                yield from _widget_entries(page, annotation)
                continue
            yield from _comment_entries(page, annotation)

    def _texts_of(self, entry: _Entry, holding: Callable[[str], bool]) -> list[str]:
        writing = entry.writing
        value = pymupdf.mupdf.pdf_dict_gets(entry.holder, entry.key)
        if writing == "text":
            return _texts_in(value)
        if writing == "required_text":
            return _texts_in(value)
        if writing == "whole":
            return _texts_within(value)
        if writing == "field_value":
            return _texts_in(value)
        if writing == "options":
            return [words for option in items_of(value) for words in _option_words(option)]
        if writing == "drawing":
            return strings_drawn_by(pdf_of(self.doc), entry.holder, holding)
        assert_never(writing)

    def _rewrite_entry(self, entry: _Entry, rewritten: Callable[[str], str]) -> bool:
        mu = pymupdf.mupdf
        writing = entry.writing
        if writing == "options":
            return self._rewrite_options(entry.holder, rewritten)
        if not _changes(self._texts_of(entry, partial(_is_changed_by, rewritten)), rewritten):
            return False
        # Words kept in a stream can't be written back in place: gone whole.
        if mu.pdf_is_stream(mu.pdf_dict_gets(entry.holder, entry.key)):
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        if writing == "whole":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        if writing == "drawing":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        if writing == "text":
            self._put_words(entry.holder, entry.key, rewritten)
            return True
        if writing == "required_text":
            kept = rewritten(mu.pdf_to_text_string(mu.pdf_dict_gets(entry.holder, entry.key)))
            mu.pdf_dict_puts(entry.holder, entry.key, mu.pdf_new_text_string(kept))
            return True
        if writing == "field_value":
            self._set_field_value(entry.holder, rewritten)
            return True
        assert_never(writing)

    def _put_words(
        self, holder: pymupdf.mupdf.PdfObj, key: str, rewritten: Callable[[str], str]
    ) -> None:
        mu = pymupdf.mupdf
        value = mu.pdf_dict_gets(holder, key)
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(holder, key, self._rewritten_list(value, rewritten))
            return
        put_text(holder, key, rewritten(mu.pdf_to_text_string(value)))

    def _set_field_value(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> None:
        """Its chosen options (I) go: they name places in a list that may have changed."""
        mu = pymupdf.mupdf
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        value = mu.pdf_dict_gets(field, FIELD_VALUE)
        # MuPDF sets only one value.
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(field, FIELD_VALUE, self._rewritten_list(value, rewritten))
            return
        kept = rewritten(mu.pdf_to_text_string(value))
        mu.pdf_set_field_value(pdf_of(self.doc), field, kept, 1)  # 1: run no script

    def _rewritten_list(
        self, written: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> pymupdf.mupdf.PdfObj:
        mu = pymupdf.mupdf
        items = items_of(written)
        kept = mu.pdf_new_array(pdf_of(self.doc), len(items))
        for item in items:
            if not mu.pdf_is_string(item):
                mu.pdf_array_push(kept, item)
                continue
            words = rewritten(mu.pdf_to_text_string(item))
            if not words:
                continue
            mu.pdf_array_push(kept, mu.pdf_new_text_string(words))
        return kept

    def _rewrite_options(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> bool:
        mu = pymupdf.mupdf
        written = [_option_words(option) for option in items_of(_options_of(field))]
        kept = [[rewritten(words) for words in option] for option in written]
        if kept == written:
            return False
        options = mu.pdf_new_array(pdf_of(self.doc), len(kept))
        for option in kept:
            if not option or not option[-1]:
                continue
            mu.pdf_array_push(options, _new_option(pdf_of(self.doc), option))
        mu.pdf_dict_put(field, mu.PDF_ENUM_NAME_Opt, options)
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        return True

    def _redrawn(
        self, changed: list[_Entry], drawing_a_word: list[_Entry]
    ) -> dict[int, set[int]]:
        mu = pymupdf.mupdf
        fields = {
            mu.pdf_to_num(entry.holder) for entry in changed if entry.writing in _FIELD_SHOWN
        }
        redrawn = self._fields_shown(fields)
        for entry in [*changed, *drawing_a_word]:
            if entry.shown is not None:
                redrawn.setdefault(entry.shown.page, set()).add(entry.shown.annotation)
        return redrawn

    def _fields_shown(self, fields: set[int]) -> dict[int, set[int]]:
        mu = pymupdf.mupdf
        shown: dict[int, set[int]] = {}
        if not fields:
            return shown
        for page, annotation in each_annotation(self.doc):
            if fields & {mu.pdf_to_num(field) for field in field_line(annotation)}:
                shown.setdefault(page, set()).add(mu.pdf_to_num(annotation))
        return shown

    def _draw_again(self, page: int, annotations: set[int]) -> None:
        mu = pymupdf.mupdf
        pdf_page = mu.pdf_page_from_fz_page(self.doc[page].this)
        for annotation in _annotations_on(pdf_page):
            if mu.pdf_to_num(mu.pdf_annot_obj(annotation)) in annotations:
                mu.pdf_dirty_annot(annotation)
        mu.pdf_update_page(pdf_page)

    def _metadata_streams(self) -> list[pymupdf.mupdf.PdfObj]:
        mu = pymupdf.mupdf
        pdf = pdf_of(self.doc)
        streams: dict[int, pymupdf.mupdf.PdfObj] = {}
        held = (
            mu.pdf_resolve_indirect(mu.pdf_new_indirect(pdf, number, 0))
            for number in range(1, mu.pdf_xref_len(pdf))
        )
        for value in chain([mu.pdf_trailer(pdf)], held):
            for dictionary in _dictionaries_in(value):
                stream = mu.pdf_dict_get(dictionary, mu.PDF_ENUM_NAME_Metadata)
                # Several may name one, as a page may name the catalog's.
                if mu.pdf_is_stream(stream):
                    streams.setdefault(mu.pdf_to_num(stream), stream)
        return list(streams.values())

    def _metadata_texts(self) -> list[str]:
        return [text for stream in self._metadata_streams() for text in _xmp_texts(stream)]

    def _rewrite_metadata(self, rewritten: Callable[[str], str]) -> None:
        for stream in self._metadata_streams():
            self._rewrite_xmp(stream, rewritten)

    def _rewrite_xmp(
        self, stream: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> None:
        written = bytes_of(stream)
        dom = xml_of(written)
        if dom is None and not _changes([readings_of(written)], rewritten):
            return
        if dom is None:
            self.doc.update_object(pymupdf.mupdf.pdf_to_num(stream), PDF_NULL)
            return
        changed = False
        for piece in xml_strings(dom):
            text = str(piece.nodeValue)
            kept = rewritten(text)
            if kept != text:
                piece.nodeValue = kept
                changed = True
        if not changed:
            return
        put_stream(self.doc, stream, xml_written(dom))


def _dictionaries_in(value: pymupdf.mupdf.PdfObj) -> Iterator[pymupdf.mupdf.PdfObj]:
    mu = pymupdf.mupdf
    waiting = [value]
    while waiting:
        node = waiting.pop()
        if mu.pdf_is_indirect(node):
            continue
        if mu.pdf_is_array(node):
            waiting += items_of(node)
            continue
        if not mu.pdf_is_dict(node):
            continue
        yield node
        waiting += values_of(node)


def _xmp_texts(stream: pymupdf.mupdf.PdfObj) -> list[str]:
    written = bytes_of(stream)
    dom = xml_of(written)
    if dom is None:
        return [readings_of(written)]
    return [str(piece.nodeValue) for piece in xml_strings(dom)]


def _texts_in(value: pymupdf.mupdf.PdfObj) -> list[str]:
    mu = pymupdf.mupdf
    items = items_of(value) if mu.pdf_is_array(value) else [value]
    return [
        _held_text_of(item)
        for item in items
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item)
    ]


def _texts_within(value: pymupdf.mupdf.PdfObj) -> list[str]:
    """Each object once: a file's tree can lead back to where it was."""
    mu = pymupdf.mupdf
    texts: list[str] = []
    waiting = [value]
    seen: set[int] = set()
    while waiting:
        item = waiting.pop()
        number = mu.pdf_to_num(item)
        if mu.pdf_is_indirect(item) and number in seen:
            continue
        seen.add(number)
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item):
            texts.append(_held_text_of(item))
            continue
        if mu.pdf_is_array(item):
            waiting += reversed(items_of(item))
            continue
        waiting += reversed(values_of(item))
    return texts


def _held_text_of(item: pymupdf.mupdf.PdfObj) -> str:
    mu = pymupdf.mupdf
    if mu.pdf_is_string(item):
        return text_of(item)
    return readings_of(bytes_of(item))


def _changes(texts: list[str], rewritten: Callable[[str], str]) -> bool:
    return any(_is_changed_by(rewritten, text) for text in texts)


def _is_changed_by(rewritten: Callable[[str], str], text: str) -> bool:
    return rewritten(text) != text


def _options_of(field: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(field, mu.PDF_ENUM_NAME_Opt)


def _option_words(option: pymupdf.mupdf.PdfObj) -> list[str]:
    mu = pymupdf.mupdf
    parts = items_of(option) if mu.pdf_is_array(option) else [option]
    return [mu.pdf_to_text_string(part) for part in parts if mu.pdf_is_string(part)]


def _new_option(pdf: pymupdf.mupdf.PdfDocument, option: list[str]) -> pymupdf.mupdf.PdfObj:
    mu = pymupdf.mupdf
    if len(option) == 1:
        return mu.pdf_new_text_string(option[0])
    pair = mu.pdf_new_array(pdf, len(option))
    for words in option:
        mu.pdf_array_push(pair, mu.pdf_new_text_string(words))
    return pair


def _is_free_text(annotation: pymupdf.mupdf.PdfObj) -> bool:
    mu = pymupdf.mupdf
    subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
    return bool(mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_FreeText))


def _annotations_on(pdf_page: pymupdf.mupdf.PdfPage) -> Iterator[pymupdf.mupdf.PdfAnnot]:
    mu = pymupdf.mupdf
    annotation = mu.pdf_first_annot(pdf_page)
    while annotation.m_internal:
        yield annotation
        annotation = mu.pdf_next_annot(annotation)
    widget = mu.pdf_first_widget(pdf_page)
    while widget.m_internal:
        yield widget
        widget = mu.pdf_next_widget(widget)


def _widget_entries(page: int, widget: pymupdf.mupdf.PdfObj) -> Iterator[_Entry]:
    mu = pymupdf.mupdf
    shown = _Shown(page, mu.pdf_to_num(widget))
    yield _Entry("form_fields", widget, _APPEARANCE, "drawing", shown=shown)
    captions = mu.pdf_dict_get(widget, mu.PDF_ENUM_NAME_MK)
    for key in _CAPTIONS:
        yield _Entry("form_fields", captions, key, "text", shown=shown)
    yield _Entry("form_fields", widget, _ANNOTATION_WORDS, "text")


def _comment_entries(page: int, annotation: pymupdf.mupdf.PdfObj) -> Iterator[_Entry]:
    mu = pymupdf.mupdf
    drawn_from_words = _is_free_text(annotation)
    shown = _Shown(page, mu.pdf_to_num(annotation)) if drawn_from_words else None
    yield _Entry("comments", annotation, _APPEARANCE, "drawing", shown=shown)
    yield _Entry("comments", annotation, _ANNOTATION_WORDS, "text", shown=shown)
    for key in _COMMENT_LABELS:
        yield _Entry("comments", annotation, key, "text", shown=shown)
    yield _Entry("comments", annotation, _FORMATTED_WORDS, "whole", shown=shown)


@dataclass(frozen=True, slots=True)
class _Shown:
    page: int
    annotation: int


@dataclass(frozen=True, slots=True, eq=False)
class _Entry:
    place: CopyPlace
    holder: pymupdf.mupdf.PdfObj
    key: str
    writing: _Writing
    shown: _Shown | None = None
