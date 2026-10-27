// A date's or an amount's editor in the slip, beside the field: it opens on the value being typed in,
// and what's chosen there is written into the field's text, as typing it would be.
import type { Editor, EditorState } from "./editor";
import { type AmountValue, type DateValue, daysIn, type EditableValue, withAmount, withDate } from "./formattedValues";
import { troublesIn, typedState } from "./typing";

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
export function chooseDate(editor: Editor, value: DateValue, date: Parameters<typeof withDate>[2]): void {
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
 * what's wrong in the server's words: the same check typing makes.
 */
export function markedDays(
  state: EditorState,
  { value, text, year, month }: { value: DateValue; text: string; year: number; month: number },
): Map<number, string> {
  const marked = new Map<number, string>();
  for (let day = 1; day <= daysIn(year, month); day++) {
    const troubles = troublesIn(state, withDate(text, value, { year, month, day })).sentences;
    if (troubles.length > 0) marked.set(day, troubles.join("\n"));
  }
  return marked;
}
