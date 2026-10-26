// The face a line goes to when its own font lacks a letter, and what even that face leaves out, as
// core/fonts/substitute.py decides from `face-letters.json` (scripts/face_files.py), so the field says it first.
import faceLetters from "../../../src/squidpdf/fonts/face-letters.json";
import type { FontInfo, SpanInfo } from "../api/types";

/** A run of code points a face draws: one alone, or the first and last of a run. */
type Run = number | readonly number[];

/** What the server's list says of one face: the face its lines go to when it lacks a letter, and what it draws. */
interface FaceLetters {
  readonly broadest: string;
  readonly letters: readonly Run[];
}

const FACES: Readonly<Record<string, FaceLetters>> = faceLetters;

/** The substitute a letter no copy of the span's font has is drawn in: the span's own, else its font's look-alike. */
export function substituteOf(span: SpanInfo, font: FontInfo): string {
  return span.substitute ?? font.look_alike;
}

/** The face a line in `face` goes to when `face` lacks a letter; undefined for a face the list hasn't. */
export function broadestOf(face: string): string | undefined {
  return FACES[face]?.broadest;
}

/** Whether `face` draws `letter`, by a binary search of its runs. A face the list hasn't is taken to. */
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

/** The letters of `text` that `face` doesn't draw, each once, in the order typed. */
function lackedBy(face: string, text: string): string[] {
  return [...new Set(text)].filter((letter) => !draws(face, letter));
}

/** A face we ship drawing a line, and the letters of it even that face can't. */
export interface Substitute {
  readonly face: string;
  readonly leftOut: readonly string[];
}

/** The face that draws `text` for a font whose look-alike is `lookAlike`: it, or its broadest, whichever leaves out fewer. */
export function substituteFor(lookAlike: string, text: string): Substitute {
  const composed = text.normalize("NFC");
  const inLookAlike = { face: lookAlike, leftOut: lackedBy(lookAlike, composed) };
  // No face could leave out fewer.
  if (inLookAlike.leftOut.length === 0) return inLookAlike;
  const broadest = broadestOf(lookAlike);
  if (broadest === undefined) return inLookAlike;
  const inBroadest = { face: broadest, leftOut: lackedBy(broadest, composed) };
  // The look-alike on a tie, as the server's min keeps the first.
  return inBroadest.leftOut.length < inLookAlike.leftOut.length ? inBroadest : inLookAlike;
}

/** How a line its font can't draw whole is drawn: in what face, the letters that sent it there, and those left out. */
export interface Drawing {
  readonly face: string;
  readonly switched: readonly string[]; // letters the span's font lacks that the face draws
  readonly leftOut: readonly string[]; // letters no face we have draws
}

/**
 * How `span` reading `text` is drawn, `missing` being the letters its font lacks: `plan_for` in
 * core/plan.py. Where only letters nothing draws are missing, the span's own font keeps the line without them.
 */
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
