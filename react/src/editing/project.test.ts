import { describe, expect, test } from "vitest";
import type { Edit, Insert, SpanInfo } from "../api/types";
import { EMPTY_HISTORY, type History, entriesOf, historyReducer } from "./history";
import { type EditedView, project, samePage, sameSpan } from "./project";

const spanOf = (id: string, page: number, text = `was ${id}`): SpanInfo => ({
  id,
  page,
  text,
  font: "Times-Roman",
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 72, y0: 100, x1: 200, y1: 112 },
  origin: [72, 110],
  fidelity: "exact",
  why: null,
});
const SPANS = [spanOf("a", 0), spanOf("b", 0), spanOf("c", 1)];
const replace = (span_id: string, text: string): Edit => ({ kind: "replace", span_id, text });
const insert: Insert = { kind: "insert", page: 1, origin: [72, 300], text: "Signed", size: 12 };

const add = (history: History, ...edits: Edit[]) => historyReducer(history, { kind: "add", edits });
const read = (history: History, previous?: EditedView) => project(SPANS, entriesOf(history), previous);

describe("what the document reads as", () => {
  test("with no edits, as it is: nothing changed, nothing to send", () => {
    const view = read(EMPTY_HISTORY);
    expect(view.spans.size).toBe(0);
    expect(view.pages.size).toBe(0);
    expect(view.edits).toEqual([]);
  });

  test("a span reads as its last replace, and only that one is sent", () => {
    const view = read(add(add(EMPTY_HISTORY, replace("a", "one")), replace("a", "two")));
    expect(view.spans.get("a")?.text).toBe("two");
    expect(view.edits).toEqual([replace("a", "two")]);
    expect(view.ids).toEqual([2]);
  });

  test("a span changed and changed back reads as untouched, and nothing is sent for it", () => {
    // Otherwise the server redraws it, in a stand-in face if the file's font can't be used,
    // while the page says nothing changed.
    const view = read(add(add(EMPTY_HISTORY, replace("a", "one")), replace("a", "was a")));
    expect(view.spans.has("a")).toBe(false);
    expect(view.pages.size).toBe(0);
    expect(view.edits).toEqual([]);
  });

  test("a redaction takes the span out, and is always sent", () => {
    const view = read(add(EMPTY_HISTORY, { kind: "redact", span_id: "b" }));
    expect(view.spans.get("b")).toMatchObject({ redacted: true, replaced: false, text: "was b" });
    expect(view.edits).toEqual([{ kind: "redact", span_id: "b" }]);
  });

  test("new text is on its own page, under its edit's id", () => {
    const view = read(add(add(EMPTY_HISTORY, replace("a", "one")), insert));
    expect(view.pages.get(1)?.inserts).toEqual([{ id: 2, edit: insert }]);
    expect(view.edits).toEqual([replace("a", "one"), insert]);
  });

  test("a page's spans are in the document's order, whatever order they were edited in", () => {
    const view = read(add(add(EMPTY_HISTORY, replace("b", "two")), replace("a", "one")));
    expect(view.pages.get(0)?.spans.map((span) => span.span.id)).toEqual(["a", "b"]);
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
