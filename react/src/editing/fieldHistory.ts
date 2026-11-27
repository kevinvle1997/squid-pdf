export interface Typing {
  readonly runs: readonly string[];
  readonly selected: readonly [number, number];
}

export interface FieldHistory {
  readonly past: readonly Typing[];
  readonly future: readonly Typing[];
  readonly joinsAt: number | null;
}

export const NO_TYPING: FieldHistory = { past: [], future: [], joinsAt: null };

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

export function undone(history: FieldHistory, now: Typing): { history: FieldHistory; to: Typing } | null {
  const to = history.past.at(-1);
  if (to === undefined) return null;
  return { history: { past: history.past.slice(0, -1), future: [...history.future, now], joinsAt: null }, to };
}

export function redone(history: FieldHistory, now: Typing): { history: FieldHistory; to: Typing } | null {
  const to = history.future.at(-1);
  if (to === undefined) return null;
  return { history: { past: [...history.past, now], future: history.future.slice(0, -1), joinsAt: null }, to };
}
