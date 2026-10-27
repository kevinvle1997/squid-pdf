// The edit history, held in the browser: the steps done, each undone whole, and those undone for redo.
// Each edit has an id of its own, so it can be taken out wherever it stands, as putting a span back does.
import type { Edit, Insert } from "../api/types";

export interface Entry {
  readonly id: number;
  readonly edit: Edit;
  readonly replaces?: number; // new text's earlier version, by its id: only the latest is sent
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
  // New text's next version, a step of its own; or, when `folds`, in the last step's place if that's the one it replaces.
  | { kind: "revise"; replaces: number; edit: Insert; folds: boolean }
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
    case "revise": {
      const last = history.done.at(-1);
      const folded = action.folds && last?.length === 1 && last[0]?.id === action.replaces ? last[0] : undefined;
      // Folded, it replaces what the last step replaced: one undo goes back past every nudge in a row.
      const replaces = folded === undefined ? action.replaces : folded.replaces;
      const entry: Entry = { id: history.next, edit: action.edit, ...(replaces !== undefined && { replaces }) };
      const done = folded === undefined ? history.done : history.done.slice(0, -1);
      return { done: [...done, [entry]], undone: [], next: history.next + 1 };
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
    default:
      return action satisfies never;
  }
}

/** Every edit in effect, in the order made. */
export function entriesOf(history: History): Entry[] {
  return history.done.flat();
}

/** The span an edit is to, if it's to one. */
export function spanOf(edit: Edit): string | undefined {
  return "span_id" in edit ? edit.span_id : undefined;
}

/** The ids of every edit in effect to `spanId`: what putting the span back takes out. */
export function touching(history: History, spanId: string): Set<number> {
  return new Set(entriesOf(history).flatMap((entry) => (spanOf(entry.edit) === spanId ? [entry.id] : [])));
}

/** The ids of every delete in effect of page `page`: what putting the page back takes out. */
export function deleting(history: History, page: number): Set<number> {
  const deletes = entriesOf(history).filter(({ edit }) => edit.kind === "delete_page" && edit.page === page);
  return new Set(deletes.map((entry) => entry.id));
}

/** Which piece of new text each entry is a version of, by its first version's id: an edit that names none is its own. */
export function textIdsOf(entries: readonly Entry[]): Map<number, number> {
  const ids = new Map<number, number>();
  for (const { id, replaces } of entries) ids.set(id, replaces === undefined ? id : (ids.get(replaces) ?? replaces));
  return ids;
}

/** The ids of every version in effect of the new text first made as `textId`: what taking it out from its margin note takes. */
export function versionsOf(history: History, textId: number): Set<number> {
  const versions = [...textIdsOf(entriesOf(history))].filter(([, text]) => text === textId);
  return new Set(versions.map(([id]) => id));
}
