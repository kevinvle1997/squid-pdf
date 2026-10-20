// Where the caret goes as a press opens a span's field: a click's caret, or the word under a double tap.
import type { SpanInfo } from "../api/types";
import { lineWidthPt } from "./fit";

/** A press that opened a span's field: how far into the span it landed, and what it selects there. */
export interface Press {
  readonly atPt: number; // from the span's start
  readonly selects: "caret" | "word";
}

// What sets a span's letters: their widths, and its size and letter spacing.
type Measure = { glyphs: Record<string, number>; span: Pick<SpanInfo, "size" | "letter_spacing_em"> };

/** A press `pressX` pixels into a span's mark, shown `shownPx` wide, in points from its start: none unlaid. */
export function pressOf(
  span: SpanInfo,
  { pressX, shownPx, selects }: { pressX: number; shownPx: number; selects: Press["selects"] },
): Press | null {
  return shownPx > 0 ? { atPt: (pressX / shownPx) * (span.bbox.x1 - span.bbox.x0), selects } : null;
}

/** What `press` selects in `text`, as the field's start and end: a caret, or the word under it. */
export function selectionAt(text: string, press: Press, measure: Measure): [number, number] {
  if (press.selects === "word") return wordAround(text, letterAt(text, press.atPt, measure));
  const caret = caretAt(text, press.atPt, measure);
  return [caret, caret];
}

/** The gap between letters nearest `atPt` points from the start of `text`, as a field puts a click's caret. */
function caretAt(text: string, atPt: number, { glyphs, span }: Measure): number {
  let widthSoFar = 0;
  let offset = 0;
  for (const letter of text) {
    const width = lineWidthPt(span, letter, glyphs);
    if (widthSoFar + width / 2 > atPt) return offset;
    widthSoFar += width;
    offset += letter.length;
  }
  return text.length;
}

/** Where in `text` the letter at `atPt` points from its start begins, counted as the field counts: in UTF-16 units. */
export function letterAt(text: string, atPt: number, { glyphs, span }: Measure): number {
  let widthSoFar = 0;
  let offset = 0;
  for (const letter of text) {
    widthSoFar += lineWidthPt(span, letter, glyphs);
    if (widthSoFar > atPt) return offset;
    offset += letter.length;
  }
  return text.length;
}

/** The word around `index`, as the field's start and end: what a double tap selects. */
export function wordAround(text: string, index: number): [number, number] {
  const isWord = (letter: string | undefined) => letter !== undefined && /\S/.test(letter);
  let start = index;
  let end = index;
  while (isWord(text[start - 1])) start--;
  while (isWord(text[end])) end++;
  return [start, end];
}
