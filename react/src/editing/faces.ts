// The faces the preview draws in: the very files the server draws with, from
// src/squidpdf/fonts, so a preview has the widths the render will have.
import type { Document, FontInfo, SpanInfo } from "../api/types";

// Longest first, so "Bold Italic" isn't read as "Italic".
const STYLES = ["Bold Italic", "Bold", "Italic", "Regular"];

/** A face's file, named as core/fonts/catalog.py names it: "Liberation Serif Bold" is LiberationSerif-Bold.ttf. */
export function fileOf(face: string): string {
  const style = STYLES.find((name) => face.endsWith(` ${name}`)) ?? "Regular";
  const family = face.endsWith(` ${style}`) ? face.slice(0, -style.length - 1) : face;
  return `${family.replaceAll(" ", "")}-${style.replaceAll(" ", "")}.ttf`;
}

/**
 * The face to preview an edit to `span` in, and the one a letter its font lacks is drawn in: the
 * face the server draws the span with when the file's own font can't, else the font's look-alike.
 * The browser has no copy of the file's own font, so the look-alike stands in for the preview only.
 */
export function previewFaceOf(span: SpanInfo, font: FontInfo): string {
  return span.substitute ?? font.look_alike;
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

/** The CSS font-family that draws `face` in the preview, once `addFaces` has put it on the document. */
export function familyOf(face: string): string {
  return urlOf(face) === undefined ? "serif" : `"${familyName(face)}", serif`;
}
