"""A document's metadata (XMP) read as XML, for the words in it. Pure: no MuPDF.

What isn't XML, or can't be read through safely, is read whole, in each encoding XMP
may use, so a word in it is still found.
"""

from __future__ import annotations

from collections.abc import Iterator
from xml.dom import minidom
from xml.parsers.expat import ExpatError

# The namespaces whose attributes XML and RDF read themselves, not text.
_XML_OWN = (
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
)
# The packet's wrapper around metadata (XMP), a note to a program; it holds no words.
_XMP_PACKET = "xpacket"
# XMP properties whose values are typed, not words, by namespace: rewritten, they'd be invalid.
_XMP_TYPED = {
    "http://purl.org/dc/elements/1.1/": ("format",),
    "http://ns.adobe.com/pdf/1.3/": ("PDFVersion", "Trapped"),
    "http://www.aiim.org/pdfa/ns/id/": ("part", "conformance", "amd", "rev"),
    "http://www.aiim.org/pdfua/ns/id/": ("part", "amd", "rev"),
    "http://ns.adobe.com/xap/1.0/t/pg/": (
        "NPages",
        "HasVisibleTransparency",
        "HasVisibleOverprint",
    ),
    "http://ns.adobe.com/xap/1.0/rights/": ("Marked",),
    "http://ns.adobe.com/xap/1.0/": ("CreateDate", "ModifyDate", "MetadataDate", "Rating"),
    "http://ns.adobe.com/xap/1.0/mm/": (
        "DocumentID",
        "InstanceID",
        "OriginalDocumentID",
        "VersionID",
    ),
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": (
        "documentID",
        "instanceID",
        "originalDocumentID",
        "versionID",
    ),
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": ("instanceID", "when"),
}
# How deep metadata (XMP) is read as XML; deeper goes whole. XMP nests a dozen or so, and
# reading or writing it takes a call per level, which Python limits.
_XML_DEEPEST = 100
# The encodings XMP may use; what isn't read as XML can't say which, so it's read in each.
_XMP_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")


def xml_of(written: bytes) -> minidom.Document | None:
    """XML as written, read; None when it can't be read, declares words, or nests too deep.

    A DOCTYPE is written back as it came, with any words it declares (an entity), so
    XML with one goes whole.
    """
    # Not XML, an unknown encoding, or one expat can't read, as UTF-32, which XMP allows.
    try:
        dom = minidom.parseString(written)
    except ExpatError, LookupError, ValueError:
        return None
    # A DOCTYPE: what it declares isn't read.
    if dom.doctype is not None:
        return None
    if _nests_deeper(dom, _XML_DEEPEST):
        return None
    return dom


def _nests_deeper(dom: minidom.Document, levels: int) -> bool:
    """Whether XML nests more than `levels` deep, read a level at a time, not by calls."""
    level: list[minidom.Node] = list(dom.childNodes)
    for _deeper in range(levels):
        level = [child for node in level for child in node.childNodes]
    return bool(level)


def xml_strings(node: minidom.Node) -> Iterator[minidom.Node]:
    """Each piece of text in XML: its text, comments, notes and attributes' values, as nodes.

    Not an attribute XML or RDF reads, the packet's wrapper, or a typed value:
    rewriting one would break the metadata.
    """
    for child in node.childNodes:
        # The packet's wrapper (xpacket), which says where the metadata starts and ends.
        if isinstance(child, minidom.ProcessingInstruction) and child.target == _XMP_PACKET:
            continue
        # Text (CDATA too), a comment, or a note to a program (a processing instruction).
        if isinstance(child, minidom.CharacterData | minidom.ProcessingInstruction):
            yield child
            continue
        # A typed value, which says when, which or what kind, not what.
        if isinstance(child, minidom.Element) and _is_typed(child):
            continue
        # An element: its attributes' values, then what's inside.
        if isinstance(child, minidom.Element):
            values = child.attributes.values()
            yield from (value for value in values if _is_xml_text(value))
            yield from xml_strings(child)


def _is_typed(node: minidom.Element | minidom.Attr) -> bool:
    """Whether an XML element or attribute is a typed metadata value, by its name."""
    # .get: most namespaces name no typed value.
    return node.localName in _XMP_TYPED.get(node.namespaceURI or "", ())


def _is_xml_text(attribute: minidom.Attr) -> bool:
    """Whether an attribute holds text: not one XML or RDF reads, nor a typed value."""
    return attribute.namespaceURI not in _XML_OWN and not _is_typed(attribute)


def readings_of(written: bytes) -> str:
    """Bytes in an encoding they don't name, read in each XMP may use, one after another."""
    return "\n".join(written.decode(encoding, "replace") for encoding in _XMP_ENCODINGS)
