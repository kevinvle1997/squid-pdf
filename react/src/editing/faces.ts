// The faces the preview draws in: the very files the server draws with, from
// src/squidpdf/fonts, so a preview has the widths the render will have.
import type { Document, FontInfo, SpanInfo } from "../api/types";
import { missingIn } from "./fit";

// Longest first, so "Bold Italic" isn't read as "Italic".
const STYLES = ["Bold Italic", "Bold", "Italic", "Regular"];

/** A face's file, named as core/fonts/catalog.py names it: "Liberation Serif Bold" is LiberationSerif-Bold.ttf. */
export function fileOf(face: string): string {
  const style = STYLES.find((name) => face.endsWith(` ${name}`)) ?? "Regular";
  const family = face.endsWith(` ${style}`) ? face.slice(0, -style.length - 1) : face;
  return `${family.replaceAll(" ", "")}-${style.replaceAll(" ", "")}.ttf`;
}

/** The substitute a letter no copy of the span's font has is drawn in: the span's own, else its font's look-alike. */
export function substituteOf(span: SpanInfo, font: FontInfo): string {
  return span.substitute ?? font.look_alike;
}

/**
 * The face to preview `text` typed over `span` in: the user's own copy of its font once the server has
 * taken it and draws the line with it; else the face a missing letter is drawn in, standing in for the
 * file's own font, which the browser has no copy of, until the server's render replaces it.
 */
export function previewFaceOf(span: SpanInfo, { font, text }: { font: FontInfo; text: string }): string {
  const own = font.attached && ownFaces.has(font.name) && span.substitute === null;
  if (own && missingIn(text, font.glyphs).length === 0) return `${OWN_COPY_PREFIX}${font.name}`;
  return substituteOf(span, font);
}

const FILES = import.meta.glob<string>("../../../src/squidpdf/fonts/*.ttf", {
  query: "?url",
  import: "default",
  eager: true,
});

/** The face a span previews in when its font isn't known. */
export const DEFAULT_FACE = "Liberation Serif Regular";

const urlOf = (face: string): string | undefined => FILES[`../../../src/squidpdf/fonts/${fileOf(face)}`];
const familyName = (face: string) => `preview ${face}`;
const added = new Set<string>();

// The user's own copies of the document's fonts, on the document to preview in, by font name. A face
// for one is named apart from ours, whose names are the catalog's.
const ownFaces = new Map<string, FontFace>();
const OWN_COPY_PREFIX = "your copy of "; // never shown: a face's name, apart from the catalog's
const ownFamily = (fontName: string) => `preview own ${fontName}`;

/** Put the user's copy of `fontName` on the page to preview in, read now so it's ready when the server takes it. */
export async function addOwnFace(fontName: string, file: Blob): Promise<void> {
  removeOwnFace(fontName);
  try {
    const face = new FontFace(ownFamily(fontName), await file.arrayBuffer());
    await face.load();
    document.fonts.add(face);
    ownFaces.set(fontName, face);
  } catch {
    // Not a font the browser reads: the preview stays in the look-alike, and the server says why.
  }
}

/** Take the user's copy of `fontName` off the document: refused, or removed. */
export function removeOwnFace(fontName: string): void {
  const face = ownFaces.get(fontName);
  if (face !== undefined) document.fonts.delete(face);
  ownFaces.delete(fontName);
}

/** Every face a document's edits can preview in: each span's similar font, each font's look-alike, and the default. */
export function facesOf({ spans, fonts }: Pick<Document, "spans" | "fonts">): Set<string> {
  const substitutes = spans.flatMap((span) => span.substitute ?? []);
  return new Set([DEFAULT_FACE, ...substitutes, ...fonts.map((font) => font.look_alike)]);
}

/**
 * Put `faces` on the document for the preview, once each, and fetch their files when the browser
 * is next idle: the first edit then draws in its face at once, and opening waits on none of them.
 */
export function addFaces(faces: Iterable<string>): void {
  const idle = window.requestIdleCallback ?? ((run: () => void) => setTimeout(run, 1));
  for (const face of faces) {
    const url = urlOf(face);
    if (url === undefined || added.has(face)) continue;
    added.add(face);
    const fontFace = new FontFace(familyName(face), `url(${url})`);
    document.fonts.add(fontFace);
    // One that fails to load previews in the browser's serif, as one we don't ship does.
    idle(() => void fontFace.load().catch(() => undefined));
  }
}

/** The CSS font-family that draws `face` in the preview, once `addFaces` or `addOwnFace` has put it on the document. */
export function familyOf(face: string): string {
  const own = face.startsWith(OWN_COPY_PREFIX) ? face.slice(OWN_COPY_PREFIX.length) : null;
  if (own !== null && ownFaces.has(own)) return `"${ownFamily(own)}", serif`;
  return urlOf(face) === undefined ? "serif" : `"${familyName(face)}", serif`;
}
