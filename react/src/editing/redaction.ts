import type { Box, Edit, Redact, RedactionInfo, Sentences, SpanInfo } from "../api/types";
import { readDocumentDates } from "./documentDates";
import { announcing, asWired, change, type Editor, type EditorState } from "./editor";
import { matchesAcross } from "./find";
import { entriesOf } from "./history";
import type { Line } from "./lines";
import { type CodePoints, type Redacted, type RedactedRange, sameRange, textIn, unitsOf, wordsAt } from "./project";
import { spanIdOf } from "./splits";
import { draftOf, lineDraftOf, spanDraftOf, typedRunsState, typedState } from "./typing";
import { fill } from "./words";

export type Selected = readonly [number, number];

const WORD_LETTER = /^[\p{L}\p{N}\p{M}]$/u;

// A space, as the server trims one (Python's str.isspace): not a zero-width no-break space, as JavaScript's \s has it.
const SPACES = new Set([
  0x9, 0xa, 0xb, 0xc, 0xd, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
  0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

/** As the server's `words_around`: `tests/editing/redacted_words_cases.json` holds both to it. */
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

export function wordsSelected(text: string, [start, end]: Selected): CodePoints | null {
  return wordsAround(text, [[...text.slice(0, start)].length, [...text.slice(0, end)].length]);
}

function fieldReadsAsPage(state: EditorState, span: SpanInfo): boolean {
  const draft = spanDraftOf(state);
  const spans = state.layout.lines.get(span.id)?.spans ?? [span];
  const asPage = spans.every((each) => textIn(state.reading, each) === each.text);
  return draft?.spanId === span.id && draft.text === wordsOf(spans) && asPage;
}

function wordsOf(spans: readonly SpanInfo[]): string {
  return spans.map((span) => span.text).join("");
}

export function takesSelection(state: EditorState, selected: Selected): boolean {
  const draft = spanDraftOf(state);
  const span = draft === null ? undefined : state.layout.spans.get(draft.spanId);
  const words = span === undefined ? "" : wordsOf(state.layout.lines.get(span.id)?.spans ?? [span]);
  return span !== undefined && fieldReadsAsPage(state, span) && wordsSelected(words, selected) !== null;
}

export function lineRedactsOf(line: Line, words: CodePoints | null): Redact[] {
  let spanStart = 0;
  return line.spans.flatMap((span): Redact[] => {
    const length = [...span.text].length;
    const from = Math.max((words?.[0] ?? 0) - spanStart, 0);
    const to = Math.min((words?.[1] ?? Number.POSITIVE_INFINITY) - spanStart, length);
    spanStart += length;
    if (words === null) return [{ kind: "redact", span_id: span.id }];
    const reached = from < to ? wordsAround(span.text, [from, to]) : null;
    if (reached === null) return [];
    const all = wordsAround(span.text, [0, length]);
    const takesAll = all !== null && reached[0] === all[0] && reached[1] === all[1];
    if (takesAll) return [{ kind: "redact", span_id: span.id }];
    return [{ kind: "redact", span_id: span.id, range: [reached[0], reached[1]] }];
  });
}

function wordsTaken(state: EditorState, span: SpanInfo, selected: Selected | null): CodePoints | null {
  if (selected === null || !fieldReadsAsPage(state, span)) return null;
  const words = wordsSelected(span.text, selected);
  const all = wordsAround(span.text, [0, [...span.text].length]);
  const takesAll = words !== null && all !== null && words[0] === all[0] && words[1] === all[1];
  return takesAll ? null : words;
}

export function redact(
  editor: Editor,
  spanId: string | null,
  { selected = null }: { selected?: Selected | null } = {},
): void {
  const state = editor.store.get();
  const { draft, reading, layout, doc } = state;
  const target = draft?.kind === "span" ? draft.spanId : spanId;
  const line = target === null ? undefined : layout.lines.get(target);
  if (line !== undefined) {
    redactLine(editor, line, { selected });
    return;
  }
  const span = target === null ? undefined : layout.spans.get(target);
  const was = span === undefined ? null : (reading.spans.get(span.id)?.redacted ?? null);
  if (span === undefined || was === "whole") return;
  const taken = wordsTaken(state, span, selected);
  const already = taken !== null && was !== null && within(unitsOf(span.text, taken), was);
  if (taken !== null && already) {
    const sentence = doc.sentences.already_redacted ?? doc.sentences.redacted;
    editor.store.set({ announcement: announcing(fill(sentence, { text: wordsAt(span.text, taken) })) });
    return;
  }
  const edit: Redact =
    taken === null ? { kind: "redact", span_id: span.id } : { kind: "redact", span_id: span.id, range: [...taken] };
  const quoted = taken === null ? textIn(reading, span) : wordsAt(span.text, taken);
  change(
    editor,
    { kind: "add", edits: [asWired(layout, edit)] },
    // Typing into it ends unkept: redaction wins.
    {
      draft: null,
      announcement: announcing(fill(doc.sentences.redacted, { text: quoted })),
      focusTo: { spanId: span.id },
      unredacting: null,
    },
  );
}

function redactLine(editor: Editor, line: Line, { selected }: { selected: Selected | null }): void {
  const state = editor.store.get();
  const { reading, doc } = state;
  const [first] = line.spans;
  const words = wordsOf(line.spans);
  const reads = first !== undefined && fieldReadsAsPage(state, first);
  const taken = selected === null || !reads ? null : wordsSelected(words, selected);
  const edits = lineRedactsOf(line, taken).map((each) => asWired(state.layout, each));
  const quoted = taken === null ? line.spans.map((span) => textIn(reading, span)).join("") : wordsAt(words, taken);
  change(
    editor,
    { kind: "add", edits },
    {
      draft: null,
      announcement: announcing(fill(doc.sentences.redacted, { text: quoted.trim() })),
      focusTo: { spanId: line.id },
      unredacting: null,
    },
  );
}

function within(range: RedactedRange, ranges: readonly RedactedRange[]): boolean {
  return ranges.some(({ start, end }) => start <= range.start && range.end <= end);
}

/** Until the reply has a range, by the letters' widths; all of a turned span: more covered, never less. */
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

export function redactedSentences(
  sentences: Sentences,
  { text, ranges }: { text: string; ranges: readonly RedactedRange[] },
) {
  const each = ranges.map(({ start, end }) => fill(sentences.redacted, { text: text.slice(start, end) }));
  return each.join(sentences.join_sentences ?? " ");
}

export function undoQuestionOf(sentences: Sentences, redacted: Redacted): string {
  return redacted === "whole" ? sentences.undo_redaction : (sentences.undo_redacted_words ?? sentences.undo_redaction);
}

export function editAnyway(editor: Editor): void {
  const { unredacting, history, layout } = editor.store.get();
  const span = unredacting === null ? undefined : layout.spans.get(unredacting.spanId);
  if (unredacting === null || span === undefined) return;
  readDocumentDates(editor);
  const line = layout.lines.get(span.id);
  // A redaction is the span's, whichever part of it asked.
  const redacted = new Set((line?.spans ?? [span]).map(({ id }) => spanIdOf(id)));
  const isRedaction = (edit: Edit) => edit.kind === "redact" && redacted.has(edit.span_id);
  const redactions = new Set(entriesOf(history).flatMap((entry) => (isRedaction(entry.edit) ? [entry.id] : [])));
  change(editor, { kind: "remove", ids: redactions }, (next) => {
    const { reading } = next;
    const { press, typed } = unredacting;
    const opened = line === undefined ? draftOf(span, { reading, press }) : lineDraftOf(line, { reading, press });
    if (typed === null) return { unredacting: null, draft: opened, focusTo: null };
    const typedIn =
      typed.runs === null
        ? typedState({ ...next, draft: opened }, typed.text)
        : typedRunsState({ ...next, draft: opened }, typed.runs);
    return { ...typedIn, unredacting: null, draft: { ...typedIn.draft, caret: typed.caret }, focusTo: null };
  });
}

export function keepRedaction(editor: Editor): void {
  const { unredacting } = editor.store.get();
  if (unredacting === null) return;
  editor.store.set({ unredacting: null, focusTo: { spanId: unredacting.spanId } });
}
