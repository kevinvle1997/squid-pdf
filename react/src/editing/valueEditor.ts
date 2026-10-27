// A date's or an amount's editor in the slip, beside the field: it opens on the value being typed in,
// and what's chosen there is written into the field's text, as typing it would be.
import { type AmountValue, withAmount } from "./amounts";
import { type CalendarDay, type DateValue, daysIn, withDate } from "./dates";
import type { Editor, EditorState } from "./editor";
import { type Fit, fitOf, troublesAddedOf } from "./fit";
import type { EditableValue } from "./formattedValues";
import { draftSpanOf, typedState } from "./typing";

/** Open the editor for `value` in the slip, Alt+Down in the field: false while nothing is typed into. */
export function openValueEditor(editor: Editor, value: EditableValue | undefined): boolean {
  const { draft } = editor.store.get();
  if (draft === null || value === undefined) return false;
  editor.store.set({ draft: { ...draft, valueEditorOpen: true } });
  return true;
}

/** Close the value's editor, keeping what's typed: focus goes back to the field. */
export function closeValueEditor(editor: Editor): void {
  const { draft } = editor.store.get();
  if (draft === null || !draft.valueEditorOpen) return;
  editor.store.set({ draft: { ...draft, valueEditorOpen: false }, focusTo: { spanId: draft.spanId } });
}

/** The day chosen in the calendar, written into the field as the page writes dates, and focus back in it. */
export function chooseDate(editor: Editor, value: DateValue, date: CalendarDay): void {
  writeValue(editor, (text) => withDate(text, value, date));
}

/** The amount given, written into the field as the page writes amounts, and focus back in it unless it's `leaving`. */
export function chooseAmount(
  editor: Editor,
  value: AmountValue,
  { amount, leaving }: { amount: number; leaving: boolean },
): void {
  writeValue(editor, (text) => withAmount(text, value, amount), { returnFocus: !leaving });
}

function writeValue(editor: Editor, write: (text: string) => string, { returnFocus = true } = {}): void {
  const state = editor.store.get();
  const { draft } = state;
  // Closed already, by Escape: what it held then is left out.
  if (draft === null || !draft.valueEditorOpen) return;
  const typed = typedState({ ...state, draft }, write(draft.text));
  editor.store.set({
    ...typed,
    draft: { ...typed.draft, valueEditorOpen: false },
    ...(returnFocus && { focusTo: { spanId: draft.spanId } }),
  });
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

// Every digit a year can be written with.
const DIGITS = "0123456789";

/**
 * Every reason a calendar day can give while it's open in `year`, for the row its reasons show on to lay out, so
 * the calendar keeps its height as the months are stepped through: each of the year's days', and one naming every
 * letter a date of another year could lack, its month's and weekday's and any digit, which is as long as any.
 */
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
      // A week of each month: every weekday's name beside each month's.
      const lacking = day <= 7 ? check.lacking(withDate(text, value, { year, month, day }) + DIGITS) : "";
      if (lacking.length > longest.length) longest = lacking;
    }
  }
  if (longest !== "") reasons.add(longest);
  return [...reasons];
}

/**
 * How a date written in `value`'s place in `text` troubles the line: what choosing one day's date adds
 * (`troublesAddedOf`), and the letters some words need that the span's font lacks and the line without the date
 * doesn't already, said as a day's are. None while nothing's typed into, or its font is unknown.
 */
function dateCheckOf(
  state: EditorState,
  { value, text }: { value: DateValue; text: string },
): { added: (date: CalendarDay) => string[]; lacking: (words: string) => string } | undefined {
  const typing = draftSpanOf(state);
  if (typing === undefined) return undefined;
  const { draft, span, font } = typing;
  const { fit: rules, sentences } = state.doc;
  const rest = fitOf(span, { font, text: text.slice(0, value.start) + text.slice(value.end), rules });
  const now = fitOf(span, { font, text, rules });
  const addedTo = (fit: Fit) => troublesAddedOf(fit, { rest, now, asked: draft.strategy, rules, sentences });
  return {
    added: (date) => addedTo(fitOf(span, { font, text: withDate(text, value, date), rules })),
    lacking: (words) => {
      // The line as it stands, lacking these letters: no longer, so it says only which.
      const { missing } = fitOf(span, { font, text: words, rules });
      return addedTo({ ...now, missing }).join("\n");
    },
  };
}

/** The marked day whose reason shows, once `day`'s is shown or hidden: one at a time. */
export function shownDayAfter(was: number | null, { day, isShown }: { day: number; isShown: boolean }): number | null {
  if (isShown) return day;
  return was === day ? null : was;
}

/** How the amount's field shows `value`, the reader's way: in its currency, where Intl knows it, to its decimals. */
export function amountFormatOptions({ format }: AmountValue): Intl.NumberFormatOptions {
  const { code, decimals } = format;
  const style: Intl.NumberFormatOptions =
    code === undefined ? { style: "decimal" } : { style: "currency", currency: code };
  return { ...style, minimumFractionDigits: decimals, maximumFractionDigits: decimals };
}
