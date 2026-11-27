// A line field's own undo of its typing, as an input keeps one: the browser makes no change in a line's field, so it
// has none of its own. Letters typed one after another are undone as one word, as an input undoes them.

/** What a line's field read, and what was selected in it, in the field's units. */
export interface Typing {
  readonly runs: readonly string[];
  readonly selected: readonly [number, number];
}

export interface FieldHistory {
  readonly past: readonly Typing[]; // what undo goes back to, the latest last
  readonly future: readonly Typing[]; // what redo brings back, the latest last
  readonly joinsAt: number | null; // where a letter typed next would join the step just made
}

export const NO_TYPING: FieldHistory = { past: [], future: [], joinsAt: null };

/**
 * `history` once what the field read was `before` and `typed` went in over its selection, the caret then at
 * `caretAfter`: one letter typed where the last left the caret joins its step, a space or anything else makes its own.
 */
export function recorded(
  history: FieldHistory,
  { before, typed, caretAfter }: { before: Typing; typed: string; caretAfter: number },
): FieldHistory {
  const [from, to] = before.selected;
  const letter = [...typed].length === 1 && !/\s/.test(typed);
  const joins = letter && from === to && from === history.joinsAt;
  const past = joins ? history.past : [...history.past, before];
  return { past, future: [], joinsAt: letter ? caretAfter : null };
}

/** The step undo goes back to from `now`, and the history after: none with nothing to undo. */
export function undone(history: FieldHistory, now: Typing): { history: FieldHistory; to: Typing } | null {
  const to = history.past.at(-1);
  if (to === undefined) return null;
  return { history: { past: history.past.slice(0, -1), future: [...history.future, now], joinsAt: null }, to };
}

/** The step redo brings back from `now`, and the history after: none with nothing undone. */
export function redone(history: FieldHistory, now: Typing): { history: FieldHistory; to: Typing } | null {
  const to = history.future.at(-1);
  if (to === undefined) return null;
  return { history: { past: [...history.past, now], future: history.future.slice(0, -1), joinsAt: null }, to };
}
