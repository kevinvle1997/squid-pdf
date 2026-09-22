# squid-pdf

A web PDF editor that corrects the text already in a document, and tells you
before you commit whether the correction will look identical to the original.

> **Status: in design.** No code yet.

## The problem

A PDF is a program that paints a page, not a document with content. Text is a
sequence of drawing operators using fonts that are usually subsetted with private
encodings, so there are no paragraphs, no lines, and often not even whole words.
Correcting one word means removing a glyph run and redrawing one that looks like
it belonged.

When the original font is not stored inside the file, every tool substitutes a
lookalike — and tells you only after you have downloaded the result.

## The contract

squid-pdf works out at load time whether each editable span can be changed
invisibly, and says so before you touch it: whether the document's own font
covers the edit, whether a metric-compatible substitute is needed, or whether
the region is an image with no text layer at all.

## Licence

[AGPL-3.0](LICENSE). Self-host it freely.
