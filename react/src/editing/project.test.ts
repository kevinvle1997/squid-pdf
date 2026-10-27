import { describe, expect, test } from "vitest";
import shared from "../../../tests/editing/page_order_cases.json";
import type { Edit, Insert } from "../api/types";
import { A4, aDeletePage, anInsert, aRedact, aReplace, aSpan } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, type History, historyReducer } from "./history";
import {
  differing,
  pageOrder,
  pagesShown,
  project,
  type Reading,
  samePage,
  sameSpan,
  shownNumberOf,
  textIn,
} from "./project";

const SPANS = [aSpan({ id: "a" }), aSpan({ id: "b" }), aSpan({ id: "c", page: 1 })];
const insert = anInsert({ page: 1, origin: [72, 300], size: 12 });

const add = (history: History, ...edits: Edit[]) => historyReducer(history, { kind: "add", edits });
const read = (history: History, previous?: Reading) =>
  project({ spans: SPANS, pages: [A4, A4] }, entriesOf(history), previous);

describe("what differs from a page's strips", () => {
  test("a redaction is a span to draw bare, and one put back reads as the original again", () => {
    const redacted = read(add(EMPTY_HISTORY, aRedact("a"))).pages.get(0);
    expect(differing(redacted, undefined)).toEqual([{ span: SPANS[0], text: "was a", redacted: true }]);
    expect(differing(undefined, redacted)).toEqual([{ span: SPANS[0], text: "was a", redacted: false }]);
  });
});

describe("what the document reads as", () => {
  test("with no edits, as it is: nothing changed, nothing to send", () => {
    const reading = read(EMPTY_HISTORY);
    expect(reading.spans.size).toBe(0);
    expect(reading.pages.size).toBe(0);
    expect(reading.edits).toEqual([]);
  });

  test("a span reads as its last replace, and only that one is sent", () => {
    const reading = read(add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("a", "two")));
    expect(reading.spans.get("a")?.text).toBe("two");
    expect(reading.edits).toEqual([aReplace("a", "two")]);
  });

  test("what a span reads is its edit's words, else its own", () => {
    const reading = read(add(EMPTY_HISTORY, aReplace("a", "one")));
    expect(textIn(reading, aSpan({ id: "a" }))).toBe("one");
    expect(textIn(reading, aSpan({ id: "b" }))).toBe("was b");
  });

  test("a span changed and changed back reads as untouched, and nothing is sent for it", () => {
    // Otherwise the server redraws it, in a substitute if the file's font can't be used,
    // while the page says nothing changed.
    const reading = read(add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("a", "was a")));
    expect(reading.spans.has("a")).toBe(false);
    expect(reading.pages.size).toBe(0);
    expect(reading.edits).toEqual([]);
  });

  test("a redaction takes the span out, and is always sent", () => {
    const reading = read(add(EMPTY_HISTORY, aRedact("b")));
    expect(reading.spans.get("b")).toMatchObject({ redacted: true, replaced: false, text: "was b" });
    expect(reading.edits).toEqual([{ kind: "redact", span_id: "b" }]);
  });

  test("new text is on its own page, under its edit's id", () => {
    const reading = read(add(add(EMPTY_HISTORY, aReplace("a", "one")), insert));
    expect(reading.pages.get(1)?.newTexts).toEqual([{ id: 2, newTextId: 2, edit: insert }]);
    expect(reading.edits).toEqual([aReplace("a", "one"), insert]);
  });

  test("a page's spans are in the document's order, whatever order they were edited in", () => {
    const reading = read(add(add(EMPTY_HISTORY, aReplace("b", "two")), aReplace("a", "one")));
    expect(reading.pages.get(0)?.spans.map((span) => span.span.id)).toEqual(["a", "b"]);
  });

  test("what an edit didn't change is the very object it was, so what draws it can skip it", () => {
    const before = read(add(EMPTY_HISTORY, aReplace("a", "one")));
    const after = read(add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("c", "three")), before);
    expect(after.spans.get("a")).toBe(before.spans.get("a"));
    expect(after.pages.get(0)).toBe(before.pages.get(0));
    expect(after.pages.get(1)).not.toBe(before.pages.get(1));
  });

  test("the same words reached again read the same as before", () => {
    const once = read(add(EMPTY_HISTORY, aReplace("a", "one")));
    const again = read(
      add(add(add(EMPTY_HISTORY, aReplace("a", "one")), aReplace("a", "x")), aReplace("a", "one")),
      once,
    );
    expect(again.spans.get("a")).toBe(once.spans.get("a"));
  });

  test("a different way to fit is a different drawing, the same words or not", () => {
    const plain = read(add(EMPTY_HISTORY, aReplace("a", "one")));
    const shrunk = read(add(EMPTY_HISTORY, aReplace("a", "one", { strategy: "shrink" })));
    expect(sameSpan(plain.spans.get("a"), shrunk.spans.get("a"))).toBe(false);
    expect(samePage(plain.pages.get(0), shrunk.pages.get(0))).toBe(false);
  });
});

describe("new text, version by version", () => {
  const moved = { ...insert, origin: [80, 310] as [number, number] };
  const revise = (
    history: History,
    { replaces, edit, folds = false }: { replaces: number; edit: Insert; folds?: boolean },
  ) => historyReducer(history, { kind: "revise", replaces, edit, folds });

  test("a later version replaces the earlier in the edits sent, read under its first version's id", () => {
    const reading = read(revise(add(add(EMPTY_HISTORY, aReplace("a", "one")), insert), { replaces: 2, edit: moved }));
    expect(reading.edits).toEqual([aReplace("a", "one"), moved]);
    expect(reading.pages.get(1)?.newTexts).toEqual([{ id: 3, newTextId: 2, edit: moved }]);
  });

  test("undo brings the earlier version back, and redo the later", () => {
    const later = revise(add(EMPTY_HISTORY, insert), { replaces: 1, edit: { ...insert, text: "Signed again" } });
    const undone = historyReducer(later, { kind: "undo" });
    expect(read(undone).edits).toEqual([insert]);
    expect(read(historyReducer(undone, { kind: "redo" })).edits).toEqual([{ ...insert, text: "Signed again" }]);
  });

  test("nudges in a row are one step: one undo goes back to where it was before them", () => {
    const nudged = (by: number) => ({ ...insert, origin: [72 + by, 300] as [number, number] });
    const once = revise(add(EMPTY_HISTORY, insert), { replaces: 1, edit: nudged(1) });
    const twice = revise(once, { replaces: 2, edit: nudged(2), folds: true });
    const thrice = revise(twice, { replaces: 3, edit: nudged(3), folds: true });
    expect(thrice.done).toHaveLength(2);
    expect(read(thrice).edits).toEqual([nudged(3)]);
    expect(read(historyReducer(thrice, { kind: "undo" })).edits).toEqual([insert]);
  });

  test("its words all taken out, it's gone: nothing reads or sends it, and undo brings it back", () => {
    const emptied = revise(add(EMPTY_HISTORY, insert), { replaces: 1, edit: { ...insert, text: " " } });
    expect(read(emptied).pages.size).toBe(0);
    expect(read(emptied).edits).toEqual([]);
    expect(read(historyReducer(emptied, { kind: "undo" })).edits).toEqual([insert]);
  });
});

describe("the pages the download keeps", () => {
  test.each(shared.cases)("$what, as the server keeps them", ({ page_count, edits, order }) => {
    const deletes = edits.map(({ page }) => aDeletePage(page));
    expect(pageOrder(page_count, deletes)).toEqual(order);
  });

  test("a page deleted is left out, its edits still read and sent; the same order stays the same object", () => {
    const edited = add(EMPTY_HISTORY, aReplace("c", "one"));
    const deleted = add(edited, aDeletePage(1));
    const reading = read(deleted);
    expect(reading.order).toEqual([0]);
    expect([...reading.deleted]).toEqual([1]);
    expect(reading.spans.get("c")?.text).toBe("one");
    expect(reading.edits).toEqual([aReplace("c", "one"), aDeletePage(1)]);
    expect(read(add(deleted, aReplace("a", "two")), reading).order).toBe(reading.order);
    expect(read(edited).order).toEqual([0, 1]);
  });
});

describe("the pages as shown", () => {
  test("number the pages kept from one; a deleted page has none, and no Split here beside it or before it", () => {
    expect(pagesShown([0, 1, 3], [A4, A4, A4, A4])).toEqual([
      { page: 0, info: A4, shownNumber: 1, splitAfter: true },
      { page: 1, info: A4, shownNumber: 2, splitAfter: true },
      { page: 2, info: A4, shownNumber: null, splitAfter: false },
      { page: 3, info: A4, shownNumber: 3, splitAfter: false },
    ]);
  });

  test("give a page's number among them, and a deleted page's as it would be put back", () => {
    expect(shownNumberOf([0, 1, 3], 3)).toBe(3);
    expect(shownNumberOf([0, 1, 3], 2)).toBe(3);
    expect(shownNumberOf([1], 0)).toBe(1);
  });
});
