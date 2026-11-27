"""What isn't XML, or can't be read safely, is read whole in each encoding XMP may use."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from xml.dom import minidom
from xml.parsers.expat import ExpatError

_XML_OWN = (
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
)
_XMP_PACKET = "xpacket"
# Typed values, not words: rewritten, they'd be invalid.
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
# Deeper goes whole: reading or writing XML takes a call per level, which Python limits.
_XML_DEEPEST = 100
_XMP_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
_PDF_PROPERTIES = "http://ns.adobe.com/pdf/1.3/"
_XMP_PROPERTIES = "http://ns.adobe.com/xap/1.0/"
_XMP_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def xml_of(written: bytes) -> minidom.Document | None:
    """A DOCTYPE is written back as it came, with what it declares: XML with one goes whole."""
    try:
        dom = minidom.parseString(written)
    except ExpatError, LookupError, ValueError:
        return None
    if dom.doctype is not None:
        return None
    if _nests_deeper(dom, _XML_DEEPEST):
        return None
    return dom


def _nests_deeper(dom: minidom.Document, levels: int) -> bool:
    level: list[minidom.Node] = list(dom.childNodes)
    for _deeper in range(levels):
        level = [child for node in level for child in node.childNodes]
    return bool(level)


def xml_strings(node: minidom.Node) -> Iterator[minidom.Node]:
    for child in node.childNodes:
        if isinstance(child, minidom.ProcessingInstruction) and child.target == _XMP_PACKET:
            continue
        if isinstance(child, minidom.CharacterData | minidom.ProcessingInstruction):
            yield child
            continue
        if isinstance(child, minidom.Element) and _is_typed(child):
            continue
        if isinstance(child, minidom.Element):
            values = child.attributes.values()
            yield from (value for value in values if _is_xml_text(value))
            yield from xml_strings(child)


def _is_typed(node: minidom.Element | minidom.Attr) -> bool:
    return node.localName in _XMP_TYPED.get(node.namespaceURI or "", ())


def _is_xml_text(attribute: minidom.Attr) -> bool:
    return attribute.namespaceURI not in _XML_OWN and not _is_typed(attribute)


def readings_of(written: bytes) -> str:
    return "\n".join(written.decode(encoding, "replace") for encoding in _XMP_ENCODINGS)


def set_changed_by(dom: minidom.Document, producer: str, when: datetime) -> bool:
    changed_at = when.astimezone(UTC).strftime(_XMP_DATE_FORMAT)
    marks = {
        (_PDF_PROPERTIES, "Producer"): producer,
        (_XMP_PROPERTIES, "ModifyDate"): changed_at,
        (_XMP_PROPERTIES, "MetadataDate"): changed_at,
    }
    found = [_set_everywhere(dom, where, value) for where, value in marks.items()]
    return any(found)


def _set_everywhere(dom: minidom.Document, where: tuple[str, str], value: str) -> bool:
    namespace, name = where
    elements = dom.getElementsByTagNameNS(namespace, name)
    for element in elements:
        for child in list(element.childNodes):
            element.removeChild(child)
        element.appendChild(dom.createTextNode(value))
    written_as = (
        element.getAttributeNodeNS(namespace, name)
        for element in dom.getElementsByTagNameNS("*", "*")
    )
    attributes = [attribute for attribute in written_as if attribute is not None]
    for attribute in attributes:
        attribute.value = value
    return bool(elements) or bool(attributes)


def xml_written(dom: minidom.Document) -> bytes:
    return "".join(node.toxml() for node in dom.childNodes).encode()
