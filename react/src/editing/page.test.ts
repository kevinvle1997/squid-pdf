import { beforeEach, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, anInsert, aSpan } from "../fixtures";
import { createEditor, type Editor } from "./editor";
import { inPageOrder, pageState, placedOnPage, spanIn } from "./page";
import { shallowEqual } from "./store";
import { edit, finish, typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

const DOC = aDoc({ spans: [aSpan({ id: "first" }), aSpan({ id: "second", page: 1 })] });

let editor: Editor;

beforeEach(() => {
  vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
  editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
});

describe("what a page draws", () => {
  test("what's sent to one span is its page's alone: focus, a scroll, the prompt before un-redacting", () => {
    edit(editor, "first", null);
    editor.store.set({
      focusTo: { spanId: "second" },
      scrollTo: { spanId: "second", count: 1, block: "nearest" },
      unredacting: { spanId: "second", press: null },
    });
    const [first, second] = [pageState(editor.store.get(), 0), pageState(editor.store.get(), 1)];
    expect(first).toMatchObject({ typingIn: "first", focusTo: null, scrollTo: null, unredacting: null });
    expect(second).toMatchObject({
      typingIn: null,
      focusTo: { spanId: "second" },
      scrollTo: { spanId: "second" },
      unredacting: "second",
    });
    expect(spanIn(second, "second")?.id).toBe("second");
    expect(spanIn(second, "first")).toBeUndefined();
  });

  test("an edit on another page leaves this one's parts as they were, so it doesn't redraw", () => {
    // The first edit takes the how-to-start note off the first page: past it, nothing there changes.
    const changeSecond = (text: string) => {
      edit(editor, "second", null);
      typeInto(editor, text);
      finish(editor, true);
    };
    changeSecond("first change");
    const before = pageState(editor.store.get(), 0);
    changeSecond("now");
    expect(shallowEqual(pageState(editor.store.get(), 0), before)).toBe(true);
    expect(shallowEqual(pageState(editor.store.get(), 1), pageState(editor.store.get(), 1))).toBe(true);
  });

  test("a page with nothing on it reads the same parts each time", () => {
    expect(shallowEqual(pageState(editor.store.get(), 5), pageState(editor.store.get(), 5))).toBe(true);
  });
});

describe("new text among a page's spans", () => {
  const at = (y: number) => aSpan({ id: `at ${y}`, bbox: { x0: 72, y0: y, x1: 200, y1: y + 12 } });
  const newTextAt = (newTextId: number, y: number) => ({
    id: newTextId,
    newTextId,
    edit: anInsert({ origin: [300, y], size: 10 }),
  });

  test("is put by the top of its line, the piece typed in again left to its field, and the field where it's typed", () => {
    const edits = { spans: [], newTexts: [newTextAt(1, 200), newTextAt(2, 300)] };
    const placed = placedOnPage({ edits, newTextAt: [72, 500], newTextTypedAgain: 2 });
    expect(placed).toEqual([
      { item: edits.newTexts[0], at: [300, 190] },
      { item: "field", at: [72, 500] },
    ]);
  });

  test("comes in Tab's order as a reader's: before the first span below its top, and after the last above it", () => {
    const spans = [at(100), at(300)];
    const order = inPageOrder(spans, [
      { item: "low", at: [72, 900] },
      { item: "middle", at: [72, 200] },
    ]);
    expect(order.map(({ span, placed }) => span?.id ?? placed)).toEqual(["at 100", "middle", "at 300", "low"]);
  });
});
