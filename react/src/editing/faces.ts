// The very files the server draws with, so a preview has the widths the render will have.
import faceFiles from "../../../src/squidpdf/fonts/face-files.json";
import type { Document, FontInfo, SpanInfo } from "../api/types";
import { missingIn } from "./fit";
import { substituteFor, substituteOf } from "./substitutes";

const FACE_FILES: Readonly<Record<string, string>> = faceFiles;

/** The browser has no copy of the file's own font: the face a missing letter sends the line to stands in until the render. */
export function previewFaceOf(span: SpanInfo, { font, text }: { font: FontInfo; text: string }): string {
  const missing = missingIn(text, font.glyphs);
  const own = font.attached && ownFaces.has(font.name) && span.substitute === null;
  if (own && missing.length === 0) return `${OWN_COPY_PREFIX}${font.name}`;
  // Its look-alike lacks a letter its broadest has: the server draws the whole line there.
  return missing.length === 0 ? substituteOf(span, font) : substituteFor(substituteOf(span, font), text).face;
}

const FILES = import.meta.glob<string>("../../../src/squidpdf/fonts/*.{ttf,otf}", {
  query: "?url",
  import: "default",
  eager: true,
});

export function isShippedFace(name: string): boolean {
  return FACE_FILES[name] !== undefined;
}

export const DEFAULT_FACE = "Liberation Serif Regular";

const urlOf = (face: string): string | undefined => {
  const file = FACE_FILES[face];
  return file === undefined ? undefined : FILES[`../../../src/squidpdf/fonts/${file}`];
};
const familyName = (face: string) => `preview ${face}`;
const added = new Set<string>();

const ownFaces = new Map<string, FontFace>();
const OWN_COPY_PREFIX = "your copy of ";
const ownFamily = (fontName: string) => `preview own ${fontName}`;

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

export function removeOwnFace(fontName: string): void {
  const face = ownFaces.get(fontName);
  if (face !== undefined) document.fonts.delete(face);
  ownFaces.delete(fontName);
}

export function facesOf({ spans, fonts }: Pick<Document, "spans" | "fonts">): Set<string> {
  const substitutes = spans.flatMap((span) => span.substitute ?? []);
  return new Set([DEFAULT_FACE, ...substitutes, ...fonts.map((font) => font.look_alike)]);
}

/** Fetched when the browser is next idle: the first edit then draws in its face at once, and opening waits on none. */
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

export function familyOf(face: string): string {
  const own = face.startsWith(OWN_COPY_PREFIX) ? face.slice(OWN_COPY_PREFIX.length) : null;
  if (own !== null && ownFaces.has(own)) return `"${ownFamily(own)}", serif`;
  return urlOf(face) === undefined ? "serif" : `"${familyName(face)}", serif`;
}
