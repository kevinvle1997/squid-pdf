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

const registered = new Set<string>();

/**
 * The CSS font-family that draws `face` in the preview. The face goes on the document once,
 * unloaded: the browser fetches its file the first time text uses it, so nothing waits on it.
 */
export function familyOf(face: string): string {
  const url = FILES[`../../../src/squidpdf/fonts/${fileOf(face)}`];
  if (url === undefined) return "serif";
  const family = `preview ${face}`;
  if (!registered.has(family)) {
    registered.add(family);
    document.fonts.add(new FontFace(family, `url(${url})`));
  }
  return `"${family}", serif`;
}
