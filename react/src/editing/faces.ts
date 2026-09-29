// The faces the preview draws in: the very files the server draws with, from
// src/squidpdf/fonts, so a preview has the widths the render will have.
import { useEffect, useState } from "react";
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
});

const loading = new Map<string, Promise<string | null>>();

/** Load a face for the preview, once; its CSS family name, or null when there's no such file. */
export function loadFace(face: string): Promise<string | null> {
  const known = loading.get(face);
  if (known !== undefined) return known;
  const load = FILES[`../../../src/squidpdf/fonts/${fileOf(face)}`];
  const family = `preview ${face}`;
  const loaded =
    load === undefined
      ? Promise.resolve(null)
      : load().then(async (url) => {
          const fontFace = new FontFace(family, `url(${url})`);
          document.fonts.add(await fontFace.load());
          return family;
        });
  loading.set(face, loaded);
  return loaded;
}

/** A face's CSS family once it has loaded, so what's drawn with it has its real widths. */
export function useFace(face: string): string | null {
  const [family, setFamily] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void loadFace(face).then((loaded) => current && setFamily(loaded));
    return () => {
      current = false;
    };
  }, [face]);
  return family;
}
