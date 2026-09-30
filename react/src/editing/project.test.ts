import { describe, expect, test } from "vitest";
import type { Edit, Insert } from "../api/types";
import { aSpan } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, type History, historyReducer } from "./history";
import { project, type Reading, samePage, sameSpan } from "./project";

const SPANS = [aSpan({ id: "a" }), aSpan({ id: "b" }), aSpan({ id: "c", page: 1 })];
const replace = (span_id: string, text: string): Edit => ({ kind: "replace", span_id, text });
const insert: Insert = { kind: "insert", page: 1, origin: [72, 300], text: "Signed", size: 12 };

const add = (history: History, ...edits: Edit[]) => historyReducer(history, { kind: "add", edits });
const read = (history: History, previous?: Reading) => project(SPANS, entriesOf(history), previous);

describe("what the document reads as", () => {
  test("with no edits, as it is: nothing changed, nothing to send", () => {
    const reading = read(EMPTY_HISTORY);
    expect(reading.spans.size).toBe(0);
    expect(reading.pages.size).toBe(0);
    expect(reading.edits).toEqual([]);
  });

  test("a span reads as its last replace, and only that one is sent", () => {
    const reading = read(add(add(EMPTY_HISTORY, replace("a", "one")), replace("a", "two")));
    expect(reading.spans.get("a")?.text).toBe("two");
    expect(reading.edits).toEqual([replace("a", "two")]);
    expect(reading.ids).toEqual([2]);
  });

  test("a span changed and changed back reads as untouched, and nothing is sent for it", () => {
    // Otherwise the server redraws it, in a stand-in face if the file's font can't be used,
    // while the page says nothing changed.
    const reading = read(add(add(EMPTY_HISTORY, replace("a", "one")), replace("a", "was a")));
    expect(reading.spans.has("a")).toBe(false);
    expect(reading.pages.size).toBe(0);
    expect(reading.edits).toEqual([]);
  });

  test("a redaction takes the span out, and is always sent", () => {
    const reading = read(add(EMPTY_HISTORY, { kind: "redact", span_id: "b" }));
    expect(reading.spans.get("b")).toMatchObject({ redacted: true, replaced: false, text: "was b" });
    expect(reading.edits).toEqual([{ kind: "redact", span_id: "b" }]);
  });

  test("new text is on its own page, under its edit's id", () => {
    const reading = read(add(add(EMPTY_HISTORY, replace("a", "one")), insert));
    expect(reading.pages.get(1)?.inserts).toEqual([{ id: 2, edit: insert }]);
    expect(reading.edits).toEqual([replace("a", "one"), insert]);
  });

  test("a page's spans are in the document's order, whatever order they were edited in", () => {
    const reading = read(add(add(EMPTY_HISTORY, replace("b", "two")), replace("a", "one")));
    expect(reading.pages.get(0)?.spans.map((span) => span.span.id)).toEqual(["a", "b"]);
  });

  test("what an edit didn't change is the very object it was, so what draws it can skip it", () => {
    const before = read(add(EMPTY_HISTORY, replace("a", "one")));
    const after = read(add(add(EMPTY_HISTORY, replace("a", "one")), replace("c", "three")), before);
    expect(after.spans.get("a")).toBe(before.spans.get("a"));
    expect(after.pages.get(0)).toBe(before.pages.get(0));
    expect(after.pages.get(1)).not.toBe(before.pages.get(1));
  });

  test("the same words reached again read the same as before", () => {
    const once = read(add(EMPTY_HISTORY, replace("a", "one")));
    const again = read(add(add(add(EMPTY_HISTORY, replace("a", "one")), replace("a", "x")), replace("a", "one")), once);
    expect(again.spans.get("a")).toBe(once.spans.get("a"));
  });

  test("a different way to fit is a different drawing, the same words or not", () => {
    const plain = read(add(EMPTY_HISTORY, replace("a", "one")));
    const shrunk = read(add(EMPTY_HISTORY, { kind: "replace", span_id: "a", text: "one", strategy: "shrink" }));
    expect(sameSpan(plain.spans.get("a"), shrunk.spans.get("a"))).toBe(false);
    expect(samePage(plain.pages.get(0), shrunk.pages.get(0))).toBe(false);
  });
});
