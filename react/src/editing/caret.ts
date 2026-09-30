// Where the caret goes as a double press opens a span's field: the word under the press.
import { widthPt } from "./fit";

/** The index in `text` of the letter at `atPt` points from its start. */
export function letterAt(text: string, atPt: number, glyphs: Record<string, number>, size: number): number {
  const letters = [...text];
  for (let index = 0; index < letters.length; index++) {
    if (widthPt(letters.slice(0, index + 1).join(""), glyphs, size) > atPt) return index;
  }
  return letters.length;
}

/** The word around `index`, as the field's start and end: what a double press selects. */
export function wordAround(text: string, index: number): [number, number] {
  const isWord = (letter: string | undefined) => letter !== undefined && /\S/.test(letter);
  let start = index;
  let end = index;
  while (isWord(text[start - 1])) start--;
  while (isWord(text[end])) end++;
  return [start, end];
}
