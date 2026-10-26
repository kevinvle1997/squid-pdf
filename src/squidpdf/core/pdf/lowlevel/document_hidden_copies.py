"""The hidden copies a file keeps outside its pages: bookmarks, fields, comments, metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
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
    bytes_of,
    each_reached,
    items_of,
    keys_of,
    metadata_stream,
    pdf_of,
    put_stream,
    put_text,
    root_of,
    text_of,
    values_of,
)
from squidpdf.core.pdf.xmp import readings_of, xml_of, xml_strings, xml_written
from squidpdf.core.types import CopyPlace, HiddenCopy

# What the file says about itself (its Info) that says when, not what.
_INFO_DATES = ("CreationDate", "ModDate")

_INFO_TITLE = "Title"  # its title, which a viewer shows in place of the file's name

_BOOKMARK_TITLE = "Title"  # what a bookmark reads

# What leads on from a bookmark: the next at its level, then the first under it.
_NEXT_BOOKMARKS = ("Next", "First")

_TAG_KIDS = ("K",)  # what leads on from a tag: the tags and content under it

_FIELD_RESET = "DV"  # what a form field goes back to when the form is reset

_FIELD_FORMATTED = "RV"  # a text field's value again, formatted, as XHTML

_FIELD_OPTIONS = "Opt"  # a choice field's options, each its words or a pair of them

_FIELD_DESCRIPTION = "TU"  # a form field's description, which a screen reader reads for it

# A signature field's seed values: what a signature put in it must be.
_FIELD_SEED_VALUES = "SV"

# An annotation's words: a comment's, or a form field's part's description of itself.
_ANNOTATION_WORDS = "Contents"

_COMMENT_LABELS = ("T", "Subj")  # a comment's author and its subject

_FORMATTED_WORDS = "RC"  # a comment's words again, formatted, as XHTML

_FIELD_CHOSEN = "I"  # a choice field's chosen options, by their places in its list

_APPEARANCE = "AP"  # an annotation's drawing (its appearance), kept apart from the page's

# A form field's part's captions (in its MK): as it rests, hovered and pressed.
_CAPTIONS = ("CA", "RC", "AC")

# How a hidden copy goes back into the file once rewritten (`_rewrite_entry`).
type _Writing = Literal["text", "required_text", "whole", "field_value", "options", "drawing"]

# The copies a form field shows on the page: once one changes, the field is drawn again.
_FIELD_SHOWN: tuple[_Writing, ...] = ("field_value", "options")


@dataclass(frozen=True, slots=True, eq=False)
class DocumentHiddenCopies:
    """The hidden copies the file keeps outside its pages, read and rewritten."""

    doc: pymupdf.Document  # open already; whoever opened it closes it

    def read(self, holding: Callable[[str], bool]) -> list[HiddenCopy]:
        """Each of the document's own hidden copies that `holding` is true of, and where."""
        found = [
            HiddenCopy(entry.place, text)
            for entry in self._document_entries()
            for text in self._texts_of(entry, holding)
        ]
        found += [HiddenCopy("metadata", text) for text in self._metadata_texts()]
        return [hidden_copy for hidden_copy in found if holding(hidden_copy.text)]

    def rewrite(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(copy)` for the document's hidden copies; one left blank goes."""
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
        # Only a drawing that drew a word or was drawn again can draw one now: read those again.
        drawn_again = {number for on_page in redrawn.values() for number in on_page}
        for drawing in drawings:
            if drawing in drawing_a_word or mu.pdf_to_num(drawing.holder) in drawn_again:
                self._rewrite_entry(drawing, rewritten)
        self._rewrite_metadata(rewritten)

    def _document_entries(self) -> Iterator[_Entry]:
        """Each place the document may keep a hidden copy of its own, and how it's put back."""
        mu = pymupdf.mupdf
        root = root_of(self.doc)
        info = mu.pdf_dict_get(mu.pdf_trailer(pdf_of(self.doc)), mu.PDF_ENUM_NAME_Info)
        for key in map(mu.pdf_to_name, keys_of(info)):
            # A date says when, not what.
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
        """What each annotation draws, on every page, and each comment's words and labels.

        A form field's part (a widget) counts as a form field; its value is read as the field's.
        """
        for page, annotation in each_annotation(self.doc):
            # A form field's part.
            if is_widget(annotation):
                yield from _widget_entries(page, annotation)
                continue
            # A comment, a link, or anything else written over the page.
            yield from _comment_entries(page, annotation)

    def _texts_of(self, entry: _Entry, holding: Callable[[str], bool]) -> list[str]:
        """The text `entry` holds: its key's, or the words of its options or drawing."""
        writing = entry.writing
        value = pymupdf.mupdf.pdf_dict_gets(entry.holder, entry.key)
        # Text, or a list of it.
        if writing == "text":
            return _texts_in(value)
        # Text the file must have.
        if writing == "required_text":
            return _texts_in(value)
        # Words that go whole once changed, wherever in it they are.
        if writing == "whole":
            return _texts_within(value)
        # A form field's value: text, or a list of it.
        if writing == "field_value":
            return _texts_in(value)
        # A choice field's options: a list of them.
        if writing == "options":
            return [words for option in items_of(value) for words in _option_words(option)]
        # An annotation's drawing: the strings it writes.
        if writing == "drawing":
            return strings_drawn_by(pdf_of(self.doc), entry.holder, holding)
        assert_never(writing)

    def _rewrite_entry(self, entry: _Entry, rewritten: Callable[[str], str]) -> bool:
        """Put `rewritten(copy)` for the copy `entry` holds, as it's put; whether it changed."""
        mu = pymupdf.mupdf
        writing = entry.writing
        # A choice field's options: a list, each option rewritten in it.
        if writing == "options":
            return self._rewrite_options(entry.holder, rewritten)
        if not _changes(self._texts_of(entry, partial(_is_changed_by, rewritten)), rewritten):
            return False
        # Words kept in a stream, which can't be written back in place: gone whole.
        if mu.pdf_is_stream(mu.pdf_dict_gets(entry.holder, entry.key)):
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # Words that go whole once changed: gone.
        if writing == "whole":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # A drawing that still draws a word: gone, so nothing shows it.
        if writing == "drawing":
            mu.pdf_dict_dels(entry.holder, entry.key)
            return True
        # Plain words, or a list of them: words of their own left stay, else they go.
        if writing == "text":
            self._put_words(entry.holder, entry.key, rewritten)
            return True
        # Text the file must have: what's left, empty if nothing is.
        if writing == "required_text":
            kept = rewritten(mu.pdf_to_text_string(mu.pdf_dict_gets(entry.holder, entry.key)))
            mu.pdf_dict_puts(entry.holder, entry.key, mu.pdf_new_text_string(kept))
            return True
        # A form field's value: set as the form sets it, so the field shows it, even empty.
        if writing == "field_value":
            self._set_field_value(entry.holder, rewritten)
            return True
        assert_never(writing)

    def _put_words(
        self, holder: pymupdf.mupdf.PdfObj, key: str, rewritten: Callable[[str], str]
    ) -> None:
        """Put `rewritten(words)` for the text under `key`, or for each text in its list.

        A text left blank goes.
        """
        mu = pymupdf.mupdf
        value = mu.pdf_dict_gets(holder, key)
        # A list of text, as a multi-select list box's value.
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(holder, key, self._rewritten_list(value, rewritten))
            return
        put_text(holder, key, rewritten(mu.pdf_to_text_string(value)))

    def _set_field_value(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> None:
        """Set a form field's value to `rewritten(value)`, as the form sets it.

        Its chosen options (I) go: they name places in a list that may have changed.
        """
        mu = pymupdf.mupdf
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        value = mu.pdf_dict_gets(field, FIELD_VALUE)
        # A list of values: MuPDF sets only one.
        if mu.pdf_is_array(value):
            mu.pdf_dict_puts(field, FIELD_VALUE, self._rewritten_list(value, rewritten))
            return
        kept = rewritten(mu.pdf_to_text_string(value))
        mu.pdf_set_field_value(pdf_of(self.doc), field, kept, 1)  # 1: run no script

    def _rewritten_list(
        self, written: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> pymupdf.mupdf.PdfObj:
        """A list with `rewritten(words)` for each text in it; a text left blank goes."""
        mu = pymupdf.mupdf
        items = items_of(written)
        kept = mu.pdf_new_array(pdf_of(self.doc), len(items))
        for item in items:
            # Not text: it stays.
            if not mu.pdf_is_string(item):
                mu.pdf_array_push(kept, item)
                continue
            words = rewritten(mu.pdf_to_text_string(item))
            # A blank line in a list says nothing.
            if not words:
                continue
            mu.pdf_array_push(kept, mu.pdf_new_text_string(words))
        return kept

    def _rewrite_options(
        self, field: pymupdf.mupdf.PdfObj, rewritten: Callable[[str], str]
    ) -> bool:
        """Put `rewritten(words)` in each of a choice field's options; whether any changed.

        An option left showing nothing goes, and with any change so do the chosen
        options (I), named by their places in the list.
        """
        mu = pymupdf.mupdf
        written = [_option_words(option) for option in items_of(_options_of(field))]
        kept = [[rewritten(words) for words in option] for option in written]
        if kept == written:
            return False
        options = mu.pdf_new_array(pdf_of(self.doc), len(kept))
        for option in kept:
            # Showing nothing, or no words at all: it goes.
            if not option or not option[-1]:
                continue
            mu.pdf_array_push(options, _new_option(pdf_of(self.doc), option))
        mu.pdf_dict_put(field, mu.PDF_ENUM_NAME_Opt, options)
        mu.pdf_dict_dels(field, _FIELD_CHOSEN)
        return True

    def _redrawn(
        self, changed: list[_Entry], drawing_a_word: list[_Entry]
    ) -> dict[int, set[int]]:
        """Annotations to redraw, by page: those drawn from words, changed or drawing a word."""
        mu = pymupdf.mupdf
        fields = {
            mu.pdf_to_num(entry.holder) for entry in changed if entry.writing in _FIELD_SHOWN
        }
        redrawn = self._fields_shown(fields)
        for entry in [*changed, *drawing_a_word]:
            # One MuPDF draws from its words, which is shown.
            if entry.shown is not None:
                redrawn.setdefault(entry.shown.page, set()).add(entry.shown.annotation)
        return redrawn

    def _fields_shown(self, fields: set[int]) -> dict[int, set[int]]:
        """Where each of `fields` shows: the page, and the field's own parts there."""
        mu = pymupdf.mupdf
        shown: dict[int, set[int]] = {}
        # No field changed: no page to read.
        if not fields:
            return shown
        for page, annotation in each_annotation(self.doc):
            if fields & {mu.pdf_to_num(field) for field in field_line(annotation)}:
                shown.setdefault(page, set()).add(mu.pdf_to_num(annotation))
        return shown

    def _draw_again(self, page: int, annotations: set[int]) -> None:
        """Have MuPDF draw these annotations on `page` again, from their words.

        One it can't draw keeps its old drawing, which is read again after.
        """
        mu = pymupdf.mupdf
        pdf_page = mu.pdf_page_from_fz_page(self.doc[page].this)
        for annotation in _annotations_on(pdf_page):
            if mu.pdf_to_num(mu.pdf_annot_obj(annotation)) in annotations:
                mu.pdf_dirty_annot(annotation)
        mu.pdf_update_page(pdf_page)

    def _metadata_texts(self) -> list[str]:
        """The text in the document's metadata (XMP), each string apart; whole if not XML."""
        stream = metadata_stream(self.doc)
        if stream is None:
            return []
        written = bytes_of(stream)
        dom = xml_of(written)
        # Not XML: any of it may hold a word, in any encoding it may be in.
        if dom is None:
            return [readings_of(written)]
        return [str(piece.nodeValue) for piece in xml_strings(dom)]

    def _rewrite_metadata(self, rewritten: Callable[[str], str]) -> None:
        """Put `rewritten(text)` for each string in the metadata (XMP); it goes if not XML."""
        mu = pymupdf.mupdf
        stream = metadata_stream(self.doc)
        if stream is None:
            return
        written = bytes_of(stream)
        dom = xml_of(written)
        # Not XML, and nothing in it would change: it stays as the file wrote it.
        if dom is None and not _changes([readings_of(written)], rewritten):
            return
        # Not XML, so not written back: it goes, as something in it would change.
        if dom is None:
            mu.pdf_dict_del(root_of(self.doc), mu.PDF_ENUM_NAME_Metadata)
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


def _texts_in(value: pymupdf.mupdf.PdfObj) -> list[str]:
    """The text a value holds: a string's, a stream's read whole, or each in a list of them."""
    mu = pymupdf.mupdf
    items = items_of(value) if mu.pdf_is_array(value) else [value]
    return [
        _held_text_of(item)
        for item in items
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item)
    ]


def _texts_within(value: pymupdf.mupdf.PdfObj) -> list[str]:
    """The text a value holds anywhere in it: in it, and in its lists and dictionaries.

    Each object once: a file's tree can lead back to where it was.
    """
    mu = pymupdf.mupdf
    texts: list[str] = []
    waiting = [value]
    seen: set[int] = set()  # objects of their own reached already, by number
    while waiting:
        item = waiting.pop()
        number = mu.pdf_to_num(item)
        # An object of its own, reached already.
        if mu.pdf_is_indirect(item) and number in seen:
            continue
        seen.add(number)
        # Text: a string, or a stream read whole.
        if mu.pdf_is_string(item) or mu.pdf_is_stream(item):
            texts.append(_held_text_of(item))
            continue
        # A list or a dictionary: each of its items is reached, in order.
        if mu.pdf_is_array(item):
            waiting += reversed(items_of(item))
            continue
        waiting += reversed(values_of(item))
    return texts


def _held_text_of(item: pymupdf.mupdf.PdfObj) -> str:
    """A string's text, all of it, or a stream's read whole, in each encoding it may be in."""
    mu = pymupdf.mupdf
    # A string: PDF says how it's encoded.
    if mu.pdf_is_string(item):
        return text_of(item)
    return readings_of(bytes_of(item))


def _changes(texts: list[str], rewritten: Callable[[str], str]) -> bool:
    """Whether `rewritten` changes any of `texts`."""
    return any(_is_changed_by(rewritten, text) for text in texts)


def _is_changed_by(rewritten: Callable[[str], str], text: str) -> bool:
    """Whether `rewritten` changes `text`: whether it holds a word `rewritten` deletes."""
    return rewritten(text) != text


def _options_of(field: pymupdf.mupdf.PdfObj) -> pymupdf.mupdf.PdfObj:
    """A choice field's options, a list; nothing there for any other field."""
    mu = pymupdf.mupdf
    return mu.pdf_dict_get(field, mu.PDF_ENUM_NAME_Opt)


def _option_words(option: pymupdf.mupdf.PdfObj) -> list[str]:
    """An option's words: its own, or a pair's, what it saves and then what it shows."""
    mu = pymupdf.mupdf
    parts = items_of(option) if mu.pdf_is_array(option) else [option]
    return [mu.pdf_to_text_string(part) for part in parts if mu.pdf_is_string(part)]


def _new_option(pdf: pymupdf.mupdf.PdfDocument, option: list[str]) -> pymupdf.mupdf.PdfObj:
    """An option for a choice field: its words alone, or a pair of them, as it came."""
    mu = pymupdf.mupdf
    if len(option) == 1:
        return mu.pdf_new_text_string(option[0])
    pair = mu.pdf_new_array(pdf, len(option))
    for words in option:
        mu.pdf_array_push(pair, mu.pdf_new_text_string(words))
    return pair


def _is_free_text(annotation: pymupdf.mupdf.PdfObj) -> bool:
    """Whether an annotation is a comment written on the page (a FreeText)."""
    mu = pymupdf.mupdf
    subtype = mu.pdf_dict_get(annotation, mu.PDF_ENUM_NAME_Subtype)
    return bool(mu.pdf_name_eq(subtype, mu.PDF_ENUM_NAME_FreeText))


def _annotations_on(pdf_page: pymupdf.mupdf.PdfPage) -> Iterator[pymupdf.mupdf.PdfAnnot]:
    """Each annotation MuPDF has loaded on a page: comments, links, then form fields."""
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
    """What a form field's part draws, its captions, and its description of itself."""
    mu = pymupdf.mupdf
    # MuPDF draws it from its words.
    shown = _Shown(page, mu.pdf_to_num(widget))
    yield _Entry("form_fields", widget, _APPEARANCE, "drawing", shown=shown)
    captions = mu.pdf_dict_get(widget, mu.PDF_ENUM_NAME_MK)
    for key in _CAPTIONS:
        yield _Entry("form_fields", captions, key, "text", shown=shown)
    yield _Entry("form_fields", widget, _ANNOTATION_WORDS, "text")


def _comment_entries(page: int, annotation: pymupdf.mupdf.PdfObj) -> Iterator[_Entry]:
    """What a comment draws, its words and its labels."""
    mu = pymupdf.mupdf
    # A FreeText MuPDF draws from its words; any other draws as it was written.
    drawn_from_words = _is_free_text(annotation)
    shown = _Shown(page, mu.pdf_to_num(annotation)) if drawn_from_words else None
    yield _Entry("comments", annotation, _APPEARANCE, "drawing", shown=shown)
    yield _Entry("comments", annotation, _ANNOTATION_WORDS, "text", shown=shown)
    for key in _COMMENT_LABELS:
        yield _Entry("comments", annotation, key, "text", shown=shown)
    yield _Entry("comments", annotation, _FORMATTED_WORDS, "whole", shown=shown)


@dataclass(frozen=True, slots=True)
class _Shown:
    """An annotation MuPDF draws from its words, a widget or a FreeText: its page and number."""

    page: int
    annotation: int


@dataclass(frozen=True, slots=True, eq=False)
class _Entry:
    """A string the document may keep a hidden copy in: where, the dictionary, its key.

    `writing` is how a new copy goes back; `shown`, the annotation to redraw once it changes.
    """

    place: CopyPlace
    holder: pymupdf.mupdf.PdfObj
    key: str
    writing: _Writing
    shown: _Shown | None = None
