// The edit history. The client holds it; every render and export sends what it adds up to.
// Undo is truncation, the same for every kind of edit, so history stays one list. Each edit
// has an id of its own, so it can be taken out by name wherever it stands: every edit to one
// span from the margin, or one edit from the history list.
import type { Edit } from "../api/types";

export interface Entry {
  readonly id: number;
  readonly edit: Edit;
}

/** One thing the user did: an edit, or several at once (a replace-all), undone together. */
export type Step = readonly Entry[];

export interface History {
  readonly done: readonly Step[];
  readonly undone: readonly Step[]; // what redo brings back, latest last
  readonly next: number; // the id the next edit gets
}

export type HistoryAction =
  | { kind: "add"; edits: readonly Edit[] }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "remove"; ids: ReadonlySet<number> }; // these edits go; the rest keep their order

export const EMPTY_HISTORY: History = { done: [], undone: [], next: 1 };

export function historyReducer(history: History, action: HistoryAction): History {
  switch (action.kind) {
    case "add": {
      if (action.edits.length === 0) return history;
      const step = action.edits.map((edit, index) => ({ id: history.next + index, edit }));
      // A new step ends what redo could bring back.
      return { done: [...history.done, step], undone: [], next: history.next + step.length };
    }
    case "undo": {
      const last = history.done.at(-1);
      if (last === undefined) return history;
      return { ...history, done: history.done.slice(0, -1), undone: [...history.undone, last] };
    }
    case "redo": {
      const next = history.undone.at(-1);
      if (next === undefined) return history;
      return { ...history, done: [...history.done, next], undone: history.undone.slice(0, -1) };
    }
    case "remove": {
      if (!entriesOf(history).some((entry) => action.ids.has(entry.id))) return history;
      const done = history.done
        .map((step) => step.filter((entry) => !action.ids.has(entry.id)))
        .filter((step) => step.length > 0);
      return { ...history, done, undone: [] };
    }
  }
}

/** Every edit in effect, in the order made. */
export function entriesOf(history: History): Entry[] {
  return history.done.flat();
}

/** The edit list the server takes: every step's edits, in the order made. */
export function editsOf(history: History): Edit[] {
  return entriesOf(history).map((entry) => entry.edit);
}

/** The span an edit is to, if it's to one. */
export function spanOf(edit: Edit): string | undefined {
  return "span_id" in edit ? edit.span_id : undefined;
}

/** The ids of every edit in effect to `spanId`: what putting the span back takes out. */
export function touching(history: History, spanId: string): Set<number> {
  return new Set(entriesOf(history).flatMap((entry) => (spanOf(entry.edit) === spanId ? [entry.id] : [])));
}

/** The text each replaced span ends with. The last edit to a span wins, as on the server. */
export function latestTexts(edits: readonly Edit[]): Map<string, string> {
  const latest = new Map<string, string>();
  for (const edit of edits) {
    if (edit.kind === "replace") latest.set(edit.span_id, edit.text);
  }
  return latest;
}

/** The spans whose final text differs between two edit lists: what a render must redraw. */
export function changedSpans(before: readonly Edit[], after: readonly Edit[]): Set<string> {
  const was = latestTexts(before);
  const now = latestTexts(after);
  const changed = new Set<string>();
  for (const id of new Set([...was.keys(), ...now.keys()])) {
    if (was.get(id) !== now.get(id)) changed.add(id);
  }
  return changed;
}
