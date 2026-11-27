// As core/fonts/substitute.py decides from `face-letters.json`, so the field says it first.
import faceLetters from "../../../src/squidpdf/fonts/face-letters.json";
import type { FontInfo, SpanInfo } from "../api/types";

type Run = number | readonly number[];

interface FaceLetters {
  readonly broadest: string;
  readonly letters: readonly Run[];
}

const FACES: Readonly<Record<string, FaceLetters>> = faceLetters;

export function substituteOf(span: SpanInfo, font: FontInfo): string {
  return span.substitute ?? font.look_alike;
}

export function broadestOf(face: string): string | undefined {
  return FACES[face]?.broadest;
}

export function draws(face: string, letter: string): boolean {
  const runs = FACES[face]?.letters;
  if (runs === undefined) return true;
  const code = letter.codePointAt(0) ?? 0;
  let low = 0;
  let high = runs.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const run = runs[middle] ?? 0;
    const [first, last] = typeof run === "number" ? [run, run] : [run[0] ?? 0, run[1] ?? 0];
    if (code < first) high = middle - 1;
    else if (code > last) low = middle + 1;
    else return true;
  }
  return false;
}

function lackedBy(face: string, text: string): string[] {
  return [...new Set(text)].filter((letter) => !draws(face, letter));
}

export interface Substitute {
  readonly face: string;
  readonly leftOut: readonly string[];
}

export function substituteFor(lookAlike: string, text: string): Substitute {
  const composed = text.normalize("NFC");
  const inLookAlike = { face: lookAlike, leftOut: lackedBy(lookAlike, composed) };
  if (inLookAlike.leftOut.length === 0) return inLookAlike;
  const broadest = broadestOf(lookAlike);
  if (broadest === undefined) return inLookAlike;
  const inBroadest = { face: broadest, leftOut: lackedBy(broadest, composed) };
  // The look-alike on a tie, as the server's min keeps the first.
  return inBroadest.leftOut.length < inLookAlike.leftOut.length ? inBroadest : inLookAlike;
}

export function drawingIn(face: string, text: string): Drawing {
  const missing = lackedBy(face, text.normalize("NFC"));
  if (missing.length === 0) return { face, switched: [], leftOut: [] };
  const substitute = substituteFor(face, text);
  return {
    face: substitute.face,
    switched: missing.filter((letter) => !substitute.leftOut.includes(letter)),
    leftOut: substitute.leftOut,
  };
}

export interface Drawing {
  readonly face: string;
  readonly switched: readonly string[];
  readonly leftOut: readonly string[];
}

export function drawingOf(
  span: SpanInfo,
  { font, text, missing }: { font: FontInfo; text: string; missing: readonly string[] },
): Drawing {
  const lookAlike = substituteOf(span, font);
  if (missing.length === 0) return { face: lookAlike, switched: [], leftOut: [] };
  const substitute = substituteFor(lookAlike, text);
  // Its file's own copy draws the rest, and nothing we have draws what that lacks: switching would gain nothing.
  if (span.in_file && missing.every((letter) => substitute.leftOut.includes(letter))) {
    return { face: lookAlike, switched: [], leftOut: missing };
  }
  const switched = missing.filter((letter) => !substitute.leftOut.includes(letter));
  return { face: substitute.face, switched, leftOut: substitute.leftOut };
}
