import { describe, expect, test } from "vitest";
import type { Replace } from "../api/types";
import { EMPTY_LOG, changedSpans, editsOf, latestTexts, logReducer, type Log } from "./log";

const replace = (span_id: string, text: string): Replace => ({ kind: "replace", span_id, text });
const add = (log: Log, ...edits: Replace[]) => logReducer(log, { kind: "add", step: edits });

describe("the edit history", () => {
  test("undo takes back the last step, and redo brings it back", () => {
    const log = add(add(EMPTY_LOG, replace("a", "one")), replace("b", "two"));
    const undone = logReducer(log, { kind: "undo" });
    expect(editsOf(undone)).toEqual([replace("a", "one")]);
    expect(editsOf(logReducer(undone, { kind: "redo" }))).toEqual(editsOf(log));
  });

  test("a replace-all is one step, so one undo takes back every place", () => {
    const log = add(EMPTY_LOG, replace("a", "2 April"), replace("b", "2 April"), replace("c", "2 April"));
    expect(editsOf(logReducer(log, { kind: "undo" }))).toEqual([]);
  });

  test("a new edit after an undo ends what redo could bring back", () => {
    const undone = logReducer(add(EMPTY_LOG, replace("a", "one")), { kind: "undo" });
    const log = add(undone, replace("b", "two"));
    expect(logReducer(log, { kind: "redo" })).toBe(log);
  });

  test("undo and redo with nothing to do change nothing", () => {
    expect(logReducer(EMPTY_LOG, { kind: "undo" })).toBe(EMPTY_LOG);
    expect(logReducer(EMPTY_LOG, { kind: "redo" })).toBe(EMPTY_LOG);
  });

  test("putting one change back from the margin keeps every other edit in order", () => {
    const log = add(add(add(EMPTY_LOG, replace("a", "one")), replace("b", "two")), replace("a", "three"));
    expect(editsOf(logReducer(log, { kind: "revert", spanId: "a" }))).toEqual([replace("b", "two")]);
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
