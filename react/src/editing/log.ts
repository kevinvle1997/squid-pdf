// The edit history. The client holds it; every render and export sends it whole.
// Undo is truncation, the same for every kind of edit, so history stays one list.
import type { Edit } from "../api/types";

/** One thing the user did: an edit, or several at once (a replace-all), undone together. */
export type Step = readonly Edit[];

export interface Log {
  readonly done: readonly Step[];
  readonly undone: readonly Step[]; // what redo brings back, latest last
}

export type LogAction =
  | { kind: "add"; step: Step }
  | { kind: "undo" }
  | { kind: "redo" }
  | { kind: "revert"; spanId: string };

export const EMPTY_LOG: Log = { done: [], undone: [] };

export function logReducer(log: Log, action: LogAction): Log {
  switch (action.kind) {
    case "add":
      // A new step ends what redo could bring back.
      return { done: [...log.done, action.step], undone: [] };
    case "undo": {
      const last = log.done.at(-1);
      if (last === undefined) return log;
      return { done: log.done.slice(0, -1), undone: [...log.undone, last] };
    }
    case "redo": {
      const next = log.undone.at(-1);
      if (next === undefined) return log;
      return { done: [...log.done, next], undone: log.undone.slice(0, -1) };
    }
    case "revert": {
      // One change put back from the margin: its edits go, the rest keep their order.
      const done = log.done
        .map((step) => step.filter((edit) => spanOf(edit) !== action.spanId))
        .filter((step) => step.length > 0);
      return { done, undone: [] };
    }
  }
}

/** The edit list the server takes: every step's edits, in the order made. */
export function editsOf(log: Log): Edit[] {
  return log.done.flat();
}

function spanOf(edit: Edit): string | undefined {
  return "span_id" in edit ? edit.span_id : undefined;
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
