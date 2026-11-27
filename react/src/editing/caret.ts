import type { SpanInfo } from "../api/types";
import { lineWidthPt } from "./fit";

export interface Press {
  readonly atPt: number;
  readonly selects: "caret" | "word";
}

type Measure = { glyphs: Record<string, number>; span: Pick<SpanInfo, "size" | "letter_spacing_em"> };

export function pressOf(
  span: Pick<SpanInfo, "bbox">,
  { pressX, shownPx, selects }: { pressX: number; shownPx: number; selects: Press["selects"] },
): Press | null {
  return shownPx > 0 ? { atPt: (pressX / shownPx) * (span.bbox.x1 - span.bbox.x0), selects } : null;
}

export function selectionAt(text: string, press: Press, measure: Measure): [number, number] {
  if (press.selects === "word") return wordAround(text, letterAt(text, press.atPt, measure));
  const caret = caretAt(text, press.atPt, measure);
  return [caret, caret];
}

export interface RunMeasure extends Measure {
  readonly startPt: number;
  readonly text: string;
}

export function selectionAcross(runs: readonly RunMeasure[], press: Press): [number, number] {
  const text = runs.map((run) => run.text).join("");
  let before = 0;
  let pressed = runs[0];
  let offset = 0;
  for (const run of runs) {
    if (run.startPt <= press.atPt) {
      pressed = run;
      before = offset;
    }
    offset += run.text.length;
  }
  if (pressed === undefined) return [0, 0];
  const atPt = press.atPt - pressed.startPt;
  if (press.selects === "word") return wordAround(text, before + letterAt(pressed.text, atPt, pressed));
  const caret = before + caretAt(pressed.text, atPt, pressed);
  return [caret, caret];
}

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

export function wordAround(text: string, index: number): [number, number] {
  const isWord = (letter: string | undefined) => letter !== undefined && /\S/.test(letter);
  let start = index;
  let end = index;
  while (isWord(text[start - 1])) start--;
  while (isWord(text[end])) end++;
  return [start, end];
}
