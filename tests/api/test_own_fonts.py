"""The user's own copy of a font: attached, checked against the file's, lent from, removed."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pymupdf
import pytest
from fastapi.testclient import TestClient

from squidpdf.api import constants as limits
from squidpdf.core.fonts.catalog import FACES, face_bytes
from squidpdf.documents import constants, store
from tests.api.conftest import upload
from tests.conftest import (
    POPPINS,
    POPPINS_TEXT,
    name_two_byte_font,
    named_only,
    named_with_widths,
)
from tests.helpers import (
    assert_at_least,
    assert_at_most,
    assert_equal,
    assert_false,
    assert_in,
    assert_problem,
    assert_true,
)

_WANTED = "Yearly Hello"  # Y, a and y aren't in the file's trimmed Poppins
_ARIAL_TEXT = "Hello there"  # what `named_only` writes


def _font_url(doc: dict, font_name: str) -> str:
    """Where the user's copy of one of the document's fonts is attached, by its listed name."""
    return f"/api/documents/{doc['id']}/fonts/{quote(font_name, safe='')}"


def _attach(client: TestClient, doc: dict, font_name: str, font_file: bytes):
    """Send a font file as the user's copy of `font_name`, raw, as the browser does."""
    return client.put(_font_url(doc, font_name), content=font_file)


def _only_font(doc: dict) -> dict:
    """The document's one font, as the browser lists it."""
    [font] = doc["fonts"]
    return font


def _fit(client: TestClient, doc: dict, text: str) -> dict:
    """What render says of `text` in place of the document's one line."""
    [span] = doc["spans"]
    body = {
        "edits": [{"kind": "replace", "span_id": span["id"], "text": text}],
        "regions": [{"page": 0}],
        "scale": 1,
    }
    rendered = client.post(f"/api/documents/{doc['id']}/render", json=body).json()
    return rendered["fits"][span["id"]]


def _kept_fonts(doc: dict) -> list[Path]:
    """The font files kept in the document's folder."""
    return sorted((store.root() / doc["id"] / "fonts").glob("[!.]*"))


@pytest.fixture
def poppins_doc(mine: TestClient, poppins_subset: str) -> dict:
    """A line in a trimmed copy of Poppins, as uploaded: Y, a and y aren't in it."""
    return upload(mine, Path(poppins_subset).read_bytes()).json()


@pytest.fixture
def arial_doc(mine: TestClient, tmp_path: Path) -> dict:
    """A line in Arial, which the file only names, with its width list, as Word writes it."""
    path = named_with_widths(
        str(tmp_path / "arial.pdf"), "Liberation Sans Regular", base_font="Arial"
    )
    return upload(mine, Path(path).read_bytes()).json()


def test_an_attached_copy_with_the_same_widths_lends_the_letters_the_file_lacks(
    mine, poppins_doc, tmp_path
):
    """Exact, drawn from it, cut down on save, and the line reads back whole."""
    font = _only_font(poppins_doc)
    before = _fit(mine, poppins_doc, _WANTED)

    attached = _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes())
    after = _fit(mine, poppins_doc, _WANTED)
    [span] = poppins_doc["spans"]
    edit = {"kind": "replace", "span_id": span["id"], "text": _WANTED}
    exported = mine.post(f"/api/documents/{poppins_doc['id']}/export", json={"edits": [edit]})
    saved = tmp_path / "exported.pdf"
    saved.write_bytes(exported.content)
    page = pymupdf.open(saved)[0]
    stored = [
        len(pymupdf.open(saved).extract_font(xref)[3]) for xref, *_ in page.get_fonts(full=True)
    ]

    assert_equal(before["missing"], ["Y", "a", "y"], "letters missing before it's attached")
    assert_equal(attached.status_code, 200, "status of an attach")
    assert_true(_only_font(attached.json())["attached"], "the font, said attached")
    assert_equal(after["missing"], [], "letters missing once it's attached")
    assert_equal(exported.status_code, 200, "export status")
    assert_in(_WANTED, page.get_text(), "the line read back from the saved file")
    assert_at_most(
        max(stored), len(POPPINS.read_bytes()) // 2, "bytes of the largest font kept"
    )


def test_a_copy_with_other_widths_is_refused_and_nothing_is_kept(mine, poppins_doc):
    font = _only_font(poppins_doc)

    refused = _attach(mine, poppins_doc, font["name"], face_bytes(FACES["Carlito Regular"]))
    read = mine.get(f"/api/documents/{poppins_doc['id']}").json()

    assert_problem(refused, "font_mismatch", 422)
    assert_false(_only_font(read)["attached"], "the font, said attached after a refusal")
    assert_equal(_kept_fonts(poppins_doc), [], "font files kept after a refusal")


def test_removing_the_copy_judges_every_span_again(mine, poppins_doc):
    font = _only_font(poppins_doc)
    _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes())

    removed = mine.delete(_font_url(poppins_doc, font["name"]))
    after = _fit(mine, poppins_doc, _WANTED)

    assert_equal(removed.status_code, 200, "status of a removal")
    assert_false(_only_font(removed.json())["attached"], "the font, said attached once removed")
    assert_equal(after["missing"], ["Y", "a", "y"], "letters missing once it's removed")
    assert_equal(_kept_fonts(poppins_doc), [], "font files kept once removed")


def test_a_font_the_file_only_names_takes_the_users_copy_as_its_own(mine, arial_doc):
    """Checked against its width list: Liberation Sans has Arial's widths; Carlito, not."""
    [span] = arial_doc["spans"]
    font = _only_font(arial_doc)

    refused = _attach(mine, arial_doc, font["name"], face_bytes(FACES["Carlito Regular"]))
    attached = _attach(
        mine, arial_doc, font["name"], face_bytes(FACES["Liberation Sans Regular"])
    ).json()
    [span_after] = attached["spans"]

    assert_equal(span["fidelity"], "substitute", "the line's fidelity with nothing attached")
    assert_equal(font["why_code"], "font_not_in_file", "why, with nothing attached")
    assert_problem(refused, "font_mismatch", 422)
    assert_equal(span_after["fidelity"], "exact", "the line's fidelity with the copy attached")
    assert_equal(span_after["text"], _ARIAL_TEXT, "the line, unchanged")


def test_a_font_only_named_without_a_width_list_is_refused(mine, tmp_path):
    """Nothing to check a copy against: taking it by its name alone would let a wrong one in."""
    path = named_only(str(tmp_path / "named.pdf"), "Arial")
    doc = upload(mine, Path(path).read_bytes()).json()

    refused = _attach(mine, doc, "Arial", face_bytes(FACES["Liberation Sans Regular"]))

    assert_problem(refused, "font_unchecked", 422)


@pytest.mark.parametrize(
    ("first_char", "status"),
    [(None, 422), ("indirect", 200)],
    ids=["it says nowhere where its list starts", "where it starts is an object apart"],
)
def test_a_width_list_is_read_as_written_or_found_unreadable(
    mine, tmp_path, first_char, status
):
    """Neither is a server error: one is read through its reference, the other can't be read."""
    path = named_with_widths(
        str(tmp_path / "arial.pdf"), "Liberation Sans Regular", base_font="Arial"
    )
    doc = pymupdf.open(path)
    [(xref, *_rest)] = doc[0].get_fonts()
    if first_char is None:
        doc.xref_set_key(xref, "FirstChar", "null")
    else:
        kept_apart = doc.get_new_xref()
        doc.update_object(kept_apart, "32")
        doc.xref_set_key(xref, "FirstChar", f"{kept_apart} 0 R")
    doc.saveIncr()
    arial_doc = upload(mine, Path(path).read_bytes()).json()

    refused = _attach(mine, arial_doc, "Arial", face_bytes(FACES["Liberation Sans Regular"]))

    if status == 422:
        assert_problem(refused, "font_unchecked", status)
    else:
        assert_equal(refused.status_code, status, "status of an attach")


def test_a_font_over_the_size_limit_is_refused_and_not_kept(mine, poppins_doc, monkeypatch):
    monkeypatch.setattr(constants, "MAX_FONT_BYTES", 1000)
    font = _only_font(poppins_doc)

    refused = _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes())

    assert_problem(refused, "too_large", 413)
    assert_equal(_kept_fonts(poppins_doc), [], "font files kept after a refusal")
    assert_equal(
        sorted((store.root() / poppins_doc["id"] / "fonts").glob("*")),
        [],
        "pieces of the refused file left in the document's folder",
    )


def test_a_font_past_the_limit_on_other_bodies_still_comes_in(mine, poppins_doc, monkeypatch):
    """A font streams in under its own limit, as an upload does, not the edit list's."""
    monkeypatch.setattr(limits, "MAX_BODY_BYTES", 1000)
    font = _only_font(poppins_doc)

    attached = _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes())

    assert_equal(attached.status_code, 200, "status of an attach past the edit list's limit")


def test_more_fonts_than_a_document_keeps_are_refused(mine, poppins_doc, monkeypatch):
    monkeypatch.setattr(constants, "MAX_FONTS", 0)
    font = _only_font(poppins_doc)

    refused = _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes())

    assert_problem(refused, "too_many_fonts", 422)


def test_a_file_that_isnt_a_font_is_refused_as_one(mine, poppins_doc):
    font = _only_font(poppins_doc)

    refused = _attach(mine, poppins_doc, font["name"], b"not a font, only some words")

    assert_problem(refused, "not_a_font", 422)
    assert_equal(_kept_fonts(poppins_doc), [], "font files kept after a refusal")


def test_a_font_the_document_doesnt_use_is_refused(mine, poppins_doc):
    refused = _attach(mine, poppins_doc, "Calibri", face_bytes(FACES["Carlito Regular"]))

    assert_problem(refused, "no_such_font", 422)


def test_attaching_a_copy_leaves_the_documents_lines_as_they_were(mine, poppins_doc):
    """The line the document drew is untouched by an attach: only the edits use the copy."""
    font = _only_font(poppins_doc)
    attached = _attach(mine, poppins_doc, font["name"], POPPINS.read_bytes()).json()

    assert_equal(
        [span["text"] for span in attached["spans"]], [POPPINS_TEXT], "the document's lines"
    )


def test_a_font_named_with_a_slash_is_still_the_documents(mine, tmp_path):
    """A PDF writes `/` in a name as `#2F`: in the path it must stay a name, not a step."""
    path = named_only(str(tmp_path / "slash.pdf"), "Odd#2FName")
    doc = upload(mine, Path(path).read_bytes()).json()

    refused = _attach(mine, doc, _only_font(doc)["name"], face_bytes(FACES["Carlito Regular"]))

    assert_problem(refused, "font_unchecked", 422)


@pytest.mark.parametrize(
    "long_name",
    [
        "Arial" + "X" * 122,  # a PDF's names run to 127 bytes; MuPDF keeps 31
        "Arial" + "#C3#A9" * 20,  # é, as two bytes each
        "AB+Arial" + "X" * 40,  # a "+" that starts no subset prefix
    ],
)
def test_a_font_only_named_by_a_long_name_takes_and_gives_up_the_users_copy(
    mine, tmp_path, long_name
):
    path = named_with_widths(
        str(tmp_path / "long.pdf"), "Liberation Sans Regular", base_font=long_name
    )
    doc = upload(mine, Path(path).read_bytes()).json()
    font = _only_font(doc)

    attached = _attach(mine, doc, font["name"], face_bytes(FACES["Liberation Sans Regular"]))
    removed = mine.delete(_font_url(doc, font["name"]))

    assert_at_least(len(font["name"]), 32, "letters in the font's name, past what MuPDF keeps")
    assert_equal(attached.status_code, 200, "status of the attach")
    assert_true(_only_font(attached.json())["attached"], "the font, said attached")
    assert_false(_only_font(removed.json())["attached"], "the font, said attached once removed")
    assert_equal(_kept_fonts(doc), [], "font files kept once removed")


def test_a_font_stored_under_a_long_name_is_read_by_it(mine, tmp_path):
    # A stored font, named as a real trimmed copy is, past what MuPDF keeps of a name.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="own", fontbuffer=POPPINS.read_bytes())
    page.insert_text((72, 96), POPPINS_TEXT, fontname="own", fontsize=12)
    [(xref, *_)] = page.get_fonts()
    name_two_byte_font(doc, xref, "Poppins-Regular" + "X" * 105)
    path = tmp_path / "long.pdf"
    doc.save(path)

    font = _only_font(upload(mine, path.read_bytes()).json())

    assert_equal(len(font["name"].encode()), 120, "bytes in the font's name, as listed")
    assert_equal(font["why_code"], None, "why the font can't draw an edit")
