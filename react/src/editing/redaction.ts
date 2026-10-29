// Redaction: a span's words taken out of the file and checked by reading back what's saved (Rule 4); a
// keyboard command, not a tool (Rule 5). Words selected in a span's field take only those words, whole.
import type { Box, Edit, Redact, RedactionInfo, Sentences, SpanInfo } from "../api/types";
import { readDocumentDates } from "./documentDates";
import { announcing, change, type Editor, type EditorState } from "./editor";
import { matchesAcross } from "./find";
import { entriesOf } from "./history";
import { type CodePoints, type Redacted, type RedactedRange, sameRange, textIn, unitsOf, wordsAt } from "./project";
import { draftOf, spanDraftOf, typedState } from "./typing";
import { fill } from "./words";

/** A selection in a text field: where it starts and ends, in the field's units (UTF-16). */
export type Selected = readonly [number, number];

// A letter of a word, as the server's check reads one: a letter, a digit, or a mark on one.
const WORD_LETTER = /^[\p{L}\p{N}\p{M}]$/u;

// A space, as the server trims one (Python's str.isspace): not a zero-width no-break space, as JavaScript's \s has it.
const SPACES = new Set([
  0x9, 0xa, 0xb, 0xc, 0xd, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
  0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

/**
 * `range` of `text`, by code point, widened to whole words, without the spaces at its ends; null if all spaces. As the
 * server's `words_around`: `tests/editing/redacted_words_cases.json` holds both to it.
 */
export function wordsAround(text: string, [start, end]: CodePoints): CodePoints | null {
  const letters = [...text];
  const isSpace = (at: number) => SPACES.has(letters[at]?.codePointAt(0) ?? 0);
  const inWord = (at: number) => WORD_LETTER.test(letters[at] ?? "");
  let from = start;
  let to = end;
  while (from < to && isSpace(from)) from++;
  while (to > from && isSpace(to - 1)) to--;
  if (from === to) return null;
  while (from > 0 && inWord(from - 1) && inWord(from)) from--;
  while (to < letters.length && inWord(to - 1) && inWord(to)) to++;
  return [from, to];
}

/** What a selection in a field reading `text` takes: its whole words, by code point; null with only spaces in it. */
export function wordsSelected(text: string, [start, end]: Selected): CodePoints | null {
  return wordsAround(text, [[...text.slice(0, start)].length, [...text.slice(0, end)].length]);
}

/** Whether `span`'s field reads as the page does, so a selection in it names the page's own words. */
function fieldReadsAsPage(state: EditorState, span: SpanInfo): boolean {
  const draft = spanDraftOf(state);
  return draft?.spanId === span.id && draft.text === span.text && textIn(state.reading, span) === span.text;
}

/**
 * Whether Cmd+Delete over `selected`, in the span's field typed in, is the redaction's: words are selected that name
 * the page's own. Over words typed, or a caret, it's the field's own.
 */
export function takesSelection(state: EditorState, selected: Selected): boolean {
  const draft = spanDraftOf(state);
  const span = draft === null ? undefined : state.layout.spans.get(draft.spanId);
  return span !== undefined && fieldReadsAsPage(state, span) && wordsSelected(span.text, selected) !== null;
}

/** The words of `span` a redaction from its field takes, by code point: null for all of it. */
function wordsTaken(state: EditorState, span: SpanInfo, selected: Selected | null): CodePoints | null {
  if (selected === null || !fieldReadsAsPage(state, span)) return null;
  const words = wordsSelected(span.text, selected);
  const all = wordsAround(span.text, [0, [...span.text].length]);
  const takesAll = words !== null && all !== null && words[0] === all[0] && words[1] === all[1];
  return takesAll ? null : words;
}

/**
 * Redact the span typed in, or else `spanId`: the words `selected` in its field, widened to whole words, while it
 * reads as the page does; else all of it. Words typed there go. Words redacted already are said to be.
 */
export function redact(
  editor: Editor,
  spanId: string | null,
  { selected = null }: { selected?: Selected | null } = {},
): void {
  const state = editor.store.get();
  const { draft, reading, layout, doc } = state;
  const target = draft?.kind === "span" ? draft.spanId : spanId;
  const span = target === null ? undefined : layout.spans.get(target);
  const was = span === undefined ? null : (reading.spans.get(span.id)?.redacted ?? null);
  if (span === undefined || was === "whole") return;
  const taken = wordsTaken(state, span, selected);
  const already = taken !== null && was !== null && within(unitsOf(span.text, taken), was);
  if (taken !== null && already) {
    // A reading persisted by an earlier build lacks the sentence: then they're said to be redacted, as they are.
    const sentence = doc.sentences.already_redacted ?? doc.sentences.redacted;
    editor.store.set({ announcement: announcing(fill(sentence, { text: wordsAt(span.text, taken) })) });
    return;
  }
  const edit: Redact =
    taken === null ? { kind: "redact", span_id: span.id } : { kind: "redact", span_id: span.id, range: [...taken] };
  const quoted = taken === null ? textIn(reading, span) : wordsAt(span.text, taken);
  change(
    editor,
    { kind: "add", edits: [edit] },
    // Typing into it ends unkept: redaction wins.
    {
      draft: null,
      announcement: announcing(fill(doc.sentences.redacted, { text: quoted })),
      focusTo: { spanId: span.id },
      unredacting: null,
    },
  );
}

/** Whether `range` lies inside one of `ranges`. */
function within(range: RedactedRange, ranges: readonly RedactedRange[]): boolean {
  return ranges.some(({ start, end }) => start <= range.start && range.end <= end);
}

/**
 * The box each of `ranges` of `span`'s words takes on the page, in points: where the server's check found its letters,
 * however the line is turned. Until its reply has the range, across a level line by the letters' widths, as Find
 * marks words; over all of a turned one, more covered than goes, never less.
 */
export function redactedBoxes(
  span: SpanInfo,
  {
    ranges,
    glyphs,
    redaction,
  }: {
    ranges: readonly RedactedRange[];
    glyphs: Record<string, number> | undefined;
    redaction: RedactionInfo | undefined;
  },
): Box[] {
  const found = (redaction?.runs ?? []).map(({ range: [start = 0, end = 0], box }) => ({
    range: unitsOf(span.text, [start, end]),
    box,
  }));
  const widePt = span.bbox.x1 - span.bbox.x0;
  const across = matchesAcross(span, { text: span.text, matches: ranges, glyphs, widePt });
  const guessed = (index: number) => (span.turned ? span.bbox : { ...span.bbox, ...(across[index] ?? {}) });
  return ranges.map((range, index) => found.find((run) => sameRange(run.range, range))?.box ?? guessed(index));
}

/** What's redacted of `text`, in `ranges`, as the server says a redaction: a whole sentence each, joined as sentences. */
export function redactedSentences(
  sentences: Sentences,
  { text, ranges }: { text: string; ranges: readonly RedactedRange[] },
) {
  const each = ranges.map(({ start, end }) => fill(sentences.redacted, { text: text.slice(start, end) }));
  // A reading persisted by an earlier build lacks the joiner: a space, as English joins sentences.
  return each.join(sentences.join_sentences ?? " ");
}

/** The question asked before editing a span undoes what's `redacted` of it: all of it, or some of its words. */
export function undoQuestionOf(sentences: Sentences, redacted: Redacted): string {
  // A reading persisted by an earlier build lacks the second: the first asks it too, if less exactly.
  return redacted === "whole" ? sentences.undo_redaction : (sentences.undo_redacted_words ?? sentences.undo_redaction);
}

/**
 * "Edit anyway": the redaction leaves the history, then the span opens for typing as it reads without it, and what was
 * typed that asked is typed in, the caret where it was.
 */
export function editAnyway(editor: Editor): void {
  const { unredacting, history, layout } = editor.store.get();
  const span = unredacting === null ? undefined : layout.spans.get(unredacting.spanId);
  if (unredacting === null || span === undefined) return;
  readDocumentDates(editor);
  const isRedaction = (edit: Edit) => edit.kind === "redact" && edit.span_id === span.id;
  const redactions = new Set(entriesOf(history).flatMap((entry) => (isRedaction(entry.edit) ? [entry.id] : [])));
  change(editor, { kind: "remove", ids: redactions }, (next) => {
    const opened = draftOf(span, { reading: next.reading, press: unredacting.press });
    const { typed } = unredacting;
    if (typed === null) return { unredacting: null, draft: opened, focusTo: null };
    const typedIn = typedState({ ...next, draft: opened }, typed.text);
    return { ...typedIn, unredacting: null, draft: { ...typedIn.draft, caret: typed.caret }, focusTo: null };
  });
}

/** "Keep redaction", or Escape: nothing changes, and focus goes back to the span. */
export function keepRedaction(editor: Editor): void {
  const { unredacting } = editor.store.get();
  if (unredacting === null) return;
  editor.store.set({ unredacting: null, focusTo: { spanId: unredacting.spanId } });
}
