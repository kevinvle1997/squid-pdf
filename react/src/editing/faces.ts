// The faces the preview draws in: the very files the server draws with, from
// src/squidpdf/fonts, so a preview has the widths the render will have.
import type { FontInfo } from "../api/types";

// Longest first, so "Bold Italic" isn't read as "Italic".
const STYLES = ["Bold Italic", "Bold", "Italic", "Regular"];

/** A face's file, named as core/fonts.py names it: "Liberation Serif Bold" is LiberationSerif-Bold.ttf. */
export function fileOf(face: string): string {
  const style = STYLES.find((name) => face.endsWith(` ${name}`)) ?? "Regular";
  const family = face.endsWith(` ${style}`) ? face.slice(0, -style.length - 1) : face;
  return `${family.replaceAll(" ", "")}-${style.replaceAll(" ", "")}.ttf`;
}

/**
 * The face to preview a span's edit in: the one the server draws with when the file's own
 * font can't be used. When it can, the browser has no copy of it, so a look-alike picked
 * from the name stands in for the preview only; the server's render replaces it.
 */
export function previewFaceOf(font: FontInfo): string {
  if (font.substitute !== null) return font.substitute;
  const name = font.name;
  const family = /mono|courier|code/i.test(name)
    ? "Liberation Mono"
    : /sans|arial|helvetica|calibri|carlito|grotesk|gothic|verdana|inter|roboto|lato/i.test(name)
      ? "Liberation Sans"
      : "Liberation Serif";
  const bold = /bold|black|heavy|semibold|demi/i.test(name);
  const italic = /italic|oblique/i.test(name);
  const style = bold && italic ? "Bold Italic" : bold ? "Bold" : italic ? "Italic" : "Regular";
  return `${family} ${style}`;
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

/** Every face a document's edits can preview in: each font's, and the default. */
export function facesOf(fonts: readonly FontInfo[]): Set<string> {
  return new Set([DEFAULT_FACE, ...fonts.map(previewFaceOf)]);
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
