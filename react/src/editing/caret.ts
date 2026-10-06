// Where the caret goes as a double press opens a span's field: the word under the press.
import { widthPt } from "./fit";

/** Where in `text` the letter at `atPt` points from its start begins, counted as the field counts: in UTF-16 units. */
export function letterAt(
  text: string,
  atPt: number,
  { glyphs, size }: { glyphs: Record<string, number>; size: number },
): number {
  let widthSoFar = 0;
  let offset = 0;
  for (const letter of text) {
    widthSoFar += widthPt(letter, glyphs, size);
    if (widthSoFar > atPt) return offset;
    offset += letter.length;
  }
  return text.length;
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
