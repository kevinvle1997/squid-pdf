import { describe, expect, test } from "vitest";
import type { Edit } from "../api/types";
import { aReplace } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, type History, historyReducer, touching } from "./history";

const add = (history: History, ...edits: Edit[]) => historyReducer(history, { kind: "add", edits });
const editsOf = (history: History) => entriesOf(history).map((entry) => entry.edit);

describe("the edit history", () => {
  test("undo takes back the last step, and redo brings it back", () => {
    const history = add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("b", "two"));
    const undone = historyReducer(history, { kind: "undo" });
    expect(editsOf(undone)).toEqual([aReplace("a", "one")]);
    expect(editsOf(historyReducer(undone, { kind: "redo" }))).toEqual(editsOf(history));
  });

  test("a replace-all is one step, so one undo takes back every place", () => {
    const history = add(EMPTY_HISTORY, aReplace("a", "2 April"), aReplace("b", "2 April"), aReplace("c", "2 April"));
    expect(editsOf(historyReducer(history, { kind: "undo" }))).toEqual([]);
  });

  test("a new edit after an undo ends what redo could bring back", () => {
    const undone = historyReducer(add(EMPTY_HISTORY, aReplace("a", "one")), { kind: "undo" });
    const history = add(undone, aReplace("b", "two"));
    expect(historyReducer(history, { kind: "redo" })).toBe(history);
  });

  test("undo, redo and an empty step with nothing to do change nothing", () => {
    expect(historyReducer(EMPTY_HISTORY, { kind: "undo" })).toBe(EMPTY_HISTORY);
    expect(historyReducer(EMPTY_HISTORY, { kind: "redo" })).toBe(EMPTY_HISTORY);
    expect(add(EMPTY_HISTORY)).toBe(EMPTY_HISTORY);
  });

  test("every edit has an id of its own, and keeps it through undo and redo", () => {
    const history = add(add(EMPTY_HISTORY, aReplace("a", "one"), aReplace("b", "two")), aReplace("c", "three"));
    expect(entriesOf(history).map((entry) => entry.id)).toEqual([1, 2, 3]);
    const again = historyReducer(historyReducer(history, { kind: "undo" }), { kind: "redo" });
    expect(entriesOf(again)).toEqual(entriesOf(history));
    // Ids are never reused, even for an edit made after an undo.
    const after = add(historyReducer(history, { kind: "undo" }), aReplace("d", "four"));
    expect(entriesOf(after).map((entry) => entry.id)).toEqual([1, 2, 4]);
  });

  test("putting one span back from the margin takes every edit to it, and keeps the rest in order", () => {
    const history = add(add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("b", "two")), aReplace("a", "three"));
    const back = historyReducer(history, { kind: "remove", ids: touching(history, "a") });
    expect(editsOf(back)).toEqual([aReplace("b", "two")]);
  });

  test("one edit can be taken out of the middle of a step by its id", () => {
    const history = add(EMPTY_HISTORY, aReplace("a", "x"), aReplace("b", "x"), aReplace("c", "x"));
    const [, middle] = entriesOf(history);
    const back = historyReducer(history, { kind: "remove", ids: new Set([middle?.id ?? 0]) });
    expect(editsOf(back)).toEqual([aReplace("a", "x"), aReplace("c", "x")]);
  });

  test("taking out an edit that isn't there changes nothing", () => {
    const history = add(EMPTY_HISTORY, aReplace("a", "one"));
    expect(historyReducer(history, { kind: "remove", ids: touching(history, "z") })).toBe(history);
  });
});
