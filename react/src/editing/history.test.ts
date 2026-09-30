import { describe, expect, test } from "vitest";
import type { Edit, Replace } from "../api/types";
import {
  EMPTY_HISTORY,
  type History,
  changedSpans,
  editsOf,
  entriesOf,
  historyReducer,
  latestTexts,
  touching,
} from "./history";

const replace = (span_id: string, text: string): Replace => ({ kind: "replace", span_id, text });
const add = (history: History, ...edits: Edit[]) => historyReducer(history, { kind: "add", edits });

describe("the edit history", () => {
  test("undo takes back the last step, and redo brings it back", () => {
    const history = add(add(EMPTY_HISTORY, replace("a", "one")), replace("b", "two"));
    const undone = historyReducer(history, { kind: "undo" });
    expect(editsOf(undone)).toEqual([replace("a", "one")]);
    expect(editsOf(historyReducer(undone, { kind: "redo" }))).toEqual(editsOf(history));
  });

  test("a replace-all is one step, so one undo takes back every place", () => {
    const history = add(EMPTY_HISTORY, replace("a", "2 April"), replace("b", "2 April"), replace("c", "2 April"));
    expect(editsOf(historyReducer(history, { kind: "undo" }))).toEqual([]);
  });

  test("a new edit after an undo ends what redo could bring back", () => {
    const undone = historyReducer(add(EMPTY_HISTORY, replace("a", "one")), { kind: "undo" });
    const history = add(undone, replace("b", "two"));
    expect(historyReducer(history, { kind: "redo" })).toBe(history);
  });

  test("undo, redo and an empty step with nothing to do change nothing", () => {
    expect(historyReducer(EMPTY_HISTORY, { kind: "undo" })).toBe(EMPTY_HISTORY);
    expect(historyReducer(EMPTY_HISTORY, { kind: "redo" })).toBe(EMPTY_HISTORY);
    expect(add(EMPTY_HISTORY)).toBe(EMPTY_HISTORY);
  });

  test("every edit has an id of its own, and keeps it through undo and redo", () => {
    const history = add(add(EMPTY_HISTORY, replace("a", "one"), replace("b", "two")), replace("c", "three"));
    expect(entriesOf(history).map((entry) => entry.id)).toEqual([1, 2, 3]);
    const again = historyReducer(historyReducer(history, { kind: "undo" }), { kind: "redo" });
    expect(entriesOf(again)).toEqual(entriesOf(history));
    // Ids are never reused, even for an edit made after an undo.
    const after = add(historyReducer(history, { kind: "undo" }), replace("d", "four"));
    expect(entriesOf(after).map((entry) => entry.id)).toEqual([1, 2, 4]);
  });

  test("putting one span back from the margin takes every edit to it, and keeps the rest in order", () => {
    const history = add(add(add(EMPTY_HISTORY, replace("a", "one")), replace("b", "two")), replace("a", "three"));
    const back = historyReducer(history, { kind: "remove", ids: touching(history, "a") });
    expect(editsOf(back)).toEqual([replace("b", "two")]);
  });

  test("one edit can be taken out of the middle of a step by its id", () => {
    const history = add(EMPTY_HISTORY, replace("a", "x"), replace("b", "x"), replace("c", "x"));
    const [, middle] = entriesOf(history);
    const back = historyReducer(history, { kind: "remove", ids: new Set([middle?.id ?? 0]) });
    expect(editsOf(back)).toEqual([replace("a", "x"), replace("c", "x")]);
  });

  test("taking out an edit that isn't there changes nothing", () => {
    const history = add(EMPTY_HISTORY, replace("a", "one"));
    expect(historyReducer(history, { kind: "remove", ids: touching(history, "z") })).toBe(history);
  });

  test("the last edit to a span is its text, as the server draws it", () => {
    expect(latestTexts([replace("a", "one"), replace("a", "two")])).toEqual(new Map([["a", "two"]]));
  });

  test("only spans whose final text changed need redrawing", () => {
    const before = [replace("a", "one"), replace("b", "two")];
    const after = [replace("a", "one"), replace("b", "three"), replace("c", "four")];
    expect(changedSpans(before, after)).toEqual(new Set(["b", "c"]));
    expect(changedSpans(after, before)).toEqual(new Set(["b", "c"]));
  });
});
