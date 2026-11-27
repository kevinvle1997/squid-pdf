// Each edit has an id of its own, so it can be taken out wherever it stands, as putting a span back does.
import type { Edit, Insert } from "../api/types";
import { partKey } from "./splits";

export interface Entry {
  readonly id: number;
  readonly edit: Edit;
  readonly replaces?: number;
}

export type Step = readonly Entry[];

export interface History {
  readonly done: readonly Step[];
  readonly undone: readonly Step[];
  readonly next: number;
}

export type HistoryAction =
  | { kind: "add"; edits: readonly Edit[] }
  | { kind: "revise"; replaces: number; edit: Insert; folds: boolean }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "remove"; ids: ReadonlySet<number> };

export const EMPTY_HISTORY: History = { done: [], undone: [], next: 1 };

export function historyReducer(history: History, action: HistoryAction): History {
  switch (action.kind) {
    case "add": {
      if (action.edits.length === 0) return history;
      const step = action.edits.map((edit, index) => ({ id: history.next + index, edit }));
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

export function entriesOf(history: History): Entry[] {
  return history.done.flat();
}

/** The spans an edit is to: one, a line's every one, each by its part's id where a split cuts it, or none. */
export function spansOf(edit: Edit): readonly string[] {
  if (edit.kind === "replace_runs") return edit.runs.map((run) => partKey(run.span_id, run.start ?? 0));
  return "span_id" in edit ? [edit.span_id] : [];
}

export function touching(history: History, spanId: string): Set<number> {
  return new Set(entriesOf(history).flatMap((entry) => (spansOf(entry.edit).includes(spanId) ? [entry.id] : [])));
}

export function deleting(history: History, page: number): Set<number> {
  const deletes = entriesOf(history).filter(({ edit }) => edit.kind === "delete_page" && edit.page === page);
  return new Set(deletes.map((entry) => entry.id));
}

export function newTextIdsOf(entries: readonly Entry[]): Map<number, number> {
  const ids = new Map<number, number>();
  for (const { id, replaces } of entries) ids.set(id, replaces === undefined ? id : (ids.get(replaces) ?? replaces));
  return ids;
}

export function versionsOf(history: History, newTextId: number): Set<number> {
  const versions = [...newTextIdsOf(entriesOf(history))].filter(([, text]) => text === newTextId);
  return new Set(versions.map(([id]) => id));
}
