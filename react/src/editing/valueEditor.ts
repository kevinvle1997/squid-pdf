// A date's or an amount's editor in the slip, beside the field: it opens on the value being typed in,
// and what's chosen there is written into the field's text, as typing it would be.

import type { FontInfo, SpanInfo } from "../api/types";
import { type AmountValue, withAmount } from "./amounts";
import { type CalendarDay, type DateValue, daysIn, withDate } from "./dates";
import type { Editor, EditorState } from "./editor";
import { recorded } from "./fieldHistory";
import { type Fit, fitOf, lineFitOf, troublesAddedOf } from "./fit";
import { type EditableValue, heldOf, heldWritten, runExtents } from "./formattedValues";
import { rulesFor } from "./lines";
import { fontOf } from "./spanFonts";
import { draftSpanOf, type SpanDraft, spanDraftOf, typedRunsState, typedState } from "./typing";

export function openValueEditor(editor: Editor, value: EditableValue | undefined): boolean {
  const draft = spanDraftOf(editor.store.get());
  if (draft === null || value === undefined) return false;
  editor.store.set({
    draft: { ...draft, valueEditorOpen: true, value: heldOf(value, draft.text) },
    ...(draft.valueEditorOpen && { focusTo: { valueEditor: draft.spanId } }),
  });
  return true;
}

export function closeValueEditor(editor: Editor): void {
  const draft = spanDraftOf(editor.store.get());
  if (draft === null || !draft.valueEditorOpen) return;
  editor.store.set({ draft: { ...draft, valueEditorOpen: false }, focusTo: { spanId: draft.spanId } });
}

export function chooseDate(editor: Editor, value: DateValue, date: CalendarDay): void {
  writeValue(editor, value, { write: (text) => withDate(text, value, date) });
}

export function chooseAmount(
  editor: Editor,
  value: AmountValue,
  { amount, leaving }: { amount: number; leaving: boolean },
): void {
  writeValue(editor, value, { write: (text) => withAmount(text, value, amount), returnFocus: !leaving });
}

function writeValue(
  editor: Editor,
  value: EditableValue,
  { write, returnFocus = true }: { write: (text: string) => string; returnFocus?: boolean },
): void {
  const state = editor.store.get();
  const draft = spanDraftOf(state);
  if (draft === null || !draft.valueEditorOpen) return;
  const typed =
    draft.runs === null
      ? typedState({ ...state, draft }, write(draft.text))
      : writtenInRun({ ...state, draft }, value, { runs: draft.runs, text: write(draft.text) });
  const held = heldWritten(value, { before: draft.text, after: typed.draft.text });
  editor.store.set({
    ...typed,
    draft: { ...typed.draft, valueEditorOpen: false, value: held },
    ...(returnFocus && { focusTo: { spanId: draft.spanId } }),
  });
}

/** A line's draft reading `text`, `value` written over in the run it lies in, which keeps its style. */
function writtenInRun(
  state: EditorState & { draft: SpanDraft },
  value: EditableValue,
  { runs, text }: { runs: readonly string[]; text: string },
): ReturnType<typeof typedRunsState> {
  const longer = text.length - state.draft.text.length;
  const written = runExtents(runs).map(({ start, end }, index) => {
    const holds = start <= value.start && value.end <= end;
    return holds ? text.slice(start, end + longer) : (runs[index] ?? "");
  });
  const next = typedRunsState(state, written);
  // Undone in the field as its typing is.
  const before = { runs, selected: [value.start, value.end] as const };
  const caretAfter = value.end + longer;
  const fieldHistory = recorded(state.draft.fieldHistory, { before, typed: "", caretAfter });
  return { ...next, draft: { ...next.draft, fieldHistory } };
}

/**
 * The days of `month` whose date, written into `text` in place of `value`, would trouble the line, each with
 * what choosing it adds in the server's words (`troublesAddedOf`): the same check typing makes, its letters set
 * against the line without the date and its length against the line as it stands, so trouble elsewhere in it, a
 * letter typed, an email broken or words that run it past its room, marks no day, least of all the one chosen.
 */
export function markedDays(
  state: EditorState,
  { value, text, year, month }: { value: DateValue; text: string; year: number; month: number },
): Map<number, string> {
  const marked = new Map<number, string>();
  const check = dateCheckOf(state, { value, text });
  if (check === undefined) return marked;
  for (let day = 1; day <= daysIn(year, month); day++) {
    const troubles = check.added({ year, month, day });
    if (troubles.length > 0) marked.set(day, troubles.join("\n"));
  }
  return marked;
}

const DIGITS = "0123456789";

/** Every reason a day can give in `year`, plus the longest another year's could, so the calendar keeps its height. */
export function reasonsAnyDayGives(
  state: EditorState,
  { value, text, year }: { value: DateValue; text: string; year: number },
): string[] {
  const check = dateCheckOf(state, { value, text });
  if (check === undefined) return [];
  const reasons = new Set<string>();
  let longest = "";
  for (let month = 1; month <= 12; month++) {
    for (let day = 1; day <= daysIn(year, month); day++) {
      const troubles = check.added({ year, month, day });
      if (troubles.length > 0) reasons.add(troubles.join("\n"));
      const lacking = day <= 7 ? check.lacking(withDate(text, value, { year, month, day }) + DIGITS) : "";
      if (lacking.length > longest.length) longest = lacking;
    }
  }
  if (longest !== "") reasons.add(longest);
  return [...reasons];
}

function dateCheckOf(
  state: EditorState,
  { value, text }: { value: DateValue; text: string },
): { added: (date: CalendarDay) => string[]; lacking: (words: string) => string } | undefined {
  const typing = draftSpanOf(state);
  if (typing === undefined) return undefined;
  const { draft } = typing;
  const { sentences } = state.doc;
  const rules = rulesFor(state.layout.lines.get(typing.span.id), state.doc.fit);
  const { fitReading, span, font } = valueFitsOf(state, { typing, value, text });
  const rest = fitReading(text.slice(0, value.start) + text.slice(value.end));
  const now = fitReading(text);
  const addedTo = (fit: Fit) => troublesAddedOf(fit, { rest, now, asked: draft.strategy, rules, sentences });
  return {
    added: (date) => addedTo(fitReading(withDate(text, value, date))),
    lacking: (words) => {
      const { missing } = fitOf(span, { font, text: words, rules });
      return addedTo({ ...now, missing }).join("\n");
    },
  };
}

/**
 * How the draft fits reading a variation of `text` that differs only at `value`: a span's by its own fit, a line's
 * with the run `value` lies in reading what changed there, by the line's; and that run's span and font.
 */
function valueFitsOf(
  state: EditorState,
  { typing, value, text }: { typing: NonNullable<ReturnType<typeof draftSpanOf>>; value: DateValue; text: string },
): { fitReading: (words: string) => Fit; span: SpanInfo; font: FontInfo } {
  const { draft, span, font } = typing;
  const rules = state.doc.fit;
  const line = state.layout.lines.get(span.id);
  const alone = { fitReading: (words: string) => fitOf(span, { font, text: words, rules }), span, font };
  if (draft.runs === null || line === undefined) return alone;
  const runs = draft.runs;
  const extents = runExtents(runs);
  const at = extents.findIndex(({ start, end }) => start <= value.start && value.end <= end);
  const run = line.spans[at];
  const runFont = run === undefined ? undefined : fontOf(state.layout.fonts, run);
  const { start = 0, end = 0 } = extents[at] ?? {};
  if (run === undefined || runFont === undefined) return alone;
  const fitReading = (words: string) => {
    const typed = line.spans.flatMap((each, index) => {
      const eachFont = fontOf(state.layout.fonts, each);
      const here = index === at ? words.slice(start, end + words.length - text.length) : (runs[index] ?? "");
      return eachFont === undefined ? [] : [{ span: each, font: eachFont, text: here }];
    });
    return lineFitOf(typed, { roomPt: line.roomPt, rules: rulesFor(line, rules) });
  };
  return { fitReading, span: run, font: runFont };
}

/** The marked day whose reason shows, once `day`'s is shown or hidden: one at a time. */
export function shownDayAfter(was: number | null, { day, isShown }: { day: number; isShown: boolean }): number | null {
  if (isShown) return day;
  return was === day ? null : was;
}

export function amountFormatOptions({ format }: AmountValue): Intl.NumberFormatOptions {
  const { code, decimals } = format;
  const style: Intl.NumberFormatOptions =
    code === undefined ? { style: "decimal" } : { style: "currency", currency: code };
  return { ...style, minimumFractionDigits: decimals, maximumFractionDigits: decimals };
}
