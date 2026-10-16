"""An upload keeps nothing that acts on its own or reaches outside the file, and says so."""

from __future__ import annotations

import hashlib
import re

import pymupdf
import pytest

from squidpdf.documents import store
from tests.api.conftest import upload
from tests.helpers import assert_equal, assert_in, assert_not_in

_LINE = "Hello there"
_WEB_LINK = "https://example.com/terms"
_LINK_BOX = "[72 200 200 220]"
# What the file says only to act on its own, attach a file, or reach outside itself.
_ACTIVE_MARKS = re.compile(
    r"/JavaScript|/JS\b|/AA\b|/OpenAction|/Launch|/GoToR|/SubmitForm|/EmbeddedFiles|/EF\b|"
    r"/FS\b|/AF\b|/XFA|/OnInstantiate|javascript:|other\.pdf|remote\.pdf|calc\.exe|\.mov"
)


def _new_object(doc: pymupdf.Document, source: str) -> int:
    """A new object in `doc` holding `source`; its number."""
    xref = doc.get_new_xref()
    doc.update_object(xref, source)
    return xref


def _new_stream(doc: pymupdf.Document, source: str, data: bytes) -> int:
    """A new stream in `doc`, its dictionary `source` and its bytes `data`; its number."""
    xref = _new_object(doc, source)
    doc.update_stream(xref, data)
    return xref


def _link(doc: pymupdf.Document, action: str) -> int:
    """A link over the same box, doing `action`; its number."""
    return _new_object(doc, f"<</Type/Annot/Subtype/Link/Rect {_LINK_BOX}/A {action}>>")


def _written_over(doc: pymupdf.Document, sources: dict[int, str]) -> bytes:
    """`doc`'s bytes, then each of `sources` written over its object, as is, in an update.

    By hand: saved through a library, every name would be written plainly.
    """
    pdf = doc.tobytes()
    listed_at = pdf.rsplit(b"startxref", 1)[1].split()[0].decode()  # its own list of objects
    update, rows = "", ""
    for number, source in sources.items():
        rows += f"{number} 1\n{len(pdf) + len(update):010d} 00000 n \n"
        update += f"{number} 0 obj\n{source}\nendobj\n"
    trailer = f"<</Size {doc.xref_length()}/Root {doc.pdf_catalog()} 0 R/Prev {listed_at}>>"
    listed = f"xref\n{rows}trailer\n{trailer}\nstartxref\n{len(pdf) + len(update)}\n%%EOF\n"
    return pdf + (update + listed).encode()


def _one_page() -> pymupdf.Document:
    """A page with a line of text on it: the upload needs something to edit."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), _LINE, fontname="helv", fontsize=12)
    return doc


@pytest.fixture
def active() -> bytes:
    """One page of text carrying each thing an upload loses, and two links it keeps.

    Scripts run on opening the file, its page, its 3D drawing and its form (XFA); links start
    a program, open another file, or run a script as a web link; files are attached or
    associated, a movie and a drawing kept elsewhere. Kept: links to its own page and the web.
    """
    doc = _one_page()
    page = doc[0]
    script = _new_object(doc, "<</S/JavaScript/JS(app.alert('opened'))>>")
    attached = _new_stream(doc, "<</Type/EmbeddedFile>>", b"the attached file")
    filespec = _new_object(doc, f"<</Type/Filespec/F(notes.txt)/EF<</F {attached} 0 R>>>>")
    scripts, files = f"<</Names[(run){script} 0 R]>>", f"<</Names[(n){filespec} 0 R]>>"
    names = f"<</JavaScript {scripts}/EmbeddedFiles {files}>>"
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "OpenAction", f"{script} 0 R")
    doc.xref_set_key(catalog, "Names", names)
    field = _new_object(doc, "<<>>")  # written over below, by hand
    form = f"<</Fields[{field} 0 R]/XFA(<script>app.alert(2)</script>)>>"
    doc.xref_set_key(catalog, "AcroForm", form)
    doc.xref_set_key(catalog, "AF", "[<</Type/Filespec/F(//server/share/notes.txt)>>]")
    on_3d = _new_stream(doc, "<</Type/3D/Subtype/U3D>>", b"")
    doc.xref_set_key(on_3d, "OnInstantiate", f"{_new_stream(doc, '<<>>', b'app.alert(3)')} 0 R")
    annotations = [
        _link(doc, "<</S/Launch/F(calc.exe)>>"),
        _link(doc, "<</S/GoToR/F(other.pdf)/D[0/Fit]>>"),
        _link(doc, "<</S/URI/URI(javascript:app.alert(1))>>"),
        _link(doc, f"<</S/GoTo/D[{page.xref} 0 R/Fit]>>"),
        _link(doc, f"<</S/URI/URI({_WEB_LINK})>>"),
        _new_object(
            doc, f"<</Type/Annot/Subtype/FileAttachment/Rect {_LINK_BOX}/FS {filespec} 0 R>>"
        ),
        _new_object(doc, f"<</Type/Annot/Subtype/3D/Rect {_LINK_BOX}/3DD {on_3d} 0 R>>"),
        _new_object(
            doc, f"<</Type/Annot/Subtype/Movie/Rect {_LINK_BOX}/Movie<</F(film.mov)>>>>"
        ),
    ]
    doc.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{n} 0 R" for n in annotations) + "]")
    # Drawn from another file: its bytes here are what a reader shows without it.
    remote = _new_stream(
        doc, "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]/F(remote.pdf)>>", b""
    )
    resources = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])  # its own object
    doc.xref_set_key(resources, "XObject", f"<</Far {remote} 0 R>>")
    page_source = doc.xref_object(page.xref, compressed=True).removesuffix(">>")
    widget = f"<</Type/Annot/Subtype/Widget/Rect {_LINK_BOX}/A#41<</K {script} 0 R>>>>"
    # The page's script, and a field's deep in a list, named escaped (#41 is A).
    return _written_over(
        doc,
        {
            page.xref: f"{page_source}/A#41<</O {script} 0 R>>>>",
            field: f"<</FT/Tx/T(total)/Kids[{widget}]>>",
        },
    )


@pytest.fixture
def plain() -> bytes:
    """One page of text that opens at its page, a web link and a tag laid out: none goes.

    The tag's `A` is its layout, as Word writes it, not an action.
    """
    doc = _one_page()
    page = doc[0]
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "OpenAction", f"[{page.xref} 0 R/Fit]")
    figure = _new_object(
        doc, f"<</Type/StructElem/S/Figure/Pg {page.xref} 0 R/A<</O/Layout>>>>"
    )
    doc.xref_set_key(catalog, "StructTreeRoot", f"<</Type/StructTreeRoot/K {figure} 0 R>>")
    link = _link(doc, f"<</S/URI/URI({_WEB_LINK})>>")
    doc.xref_set_key(page.xref, "Annots", f"[{link} 0 R]")
    return doc.tobytes()


def _everything_in(pdf: bytes) -> str:
    """Every object the file keeps, as written, its trailer included."""
    doc = pymupdf.open(stream=pdf)
    return "\n".join(
        [doc.xref_object(xref) for xref in range(1, doc.xref_length())] + [doc.pdf_trailer()]
    )


def test_an_upload_keeps_nothing_that_acts_on_its_own_or_reaches_out_and_says_so(mine, active):
    """A hostile file can't script, attach or fetch through the file the user downloads."""
    doc = upload(mine, active).json()
    exported = mine.post(f"/api/documents/{doc['id']}/export", json={"edits": []}).content

    assert_in(
        "active_content_dropped", [notice["type"] for notice in doc["notices"]], "notices"
    )
    left = _ACTIVE_MARKS.findall(_everything_in(exported))
    assert_equal(left, [], "what's left that acts on its own or reaches out")
    saved = pymupdf.open(stream=exported)
    page_script = saved.xref_get_key(saved[0].xref, "AA")
    assert_equal(page_script, ("null", "null"), "the page's script, its name escaped")
    assert_equal(saved.embfile_count(), 0, "files attached")
    links = saved[0].get_links()
    assert_in(_WEB_LINK, [link.get("uri") for link in links], "the web link kept")
    assert_in(pymupdf.LINK_GOTO, [link["kind"] for link in links], "the link to its page kept")
    assert_in(_LINE, saved[0].get_text(), "the page's text")


def test_a_file_with_nothing_active_is_kept_as_sent(mine, plain):
    """Only a file that had something to take out is written again, and only then is it said."""
    doc = upload(mine, plain).json()

    kept = (store.root() / doc["id"] / store.ORIGINAL).read_bytes()
    assert_equal(hashlib.sha256(kept).hexdigest(), hashlib.sha256(plain).hexdigest(), "as sent")
    assert_not_in("active_content_dropped", [n["type"] for n in doc["notices"]], "notices")
