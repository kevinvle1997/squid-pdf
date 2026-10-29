// A view test though nothing's drawn: what a key was pressed on is an element, which only a browser has.
import { describe, expect, test } from "vitest";
import { fellToNothing, isOnMarginNote, isTyping, keepFocusOnPress, pageOn, spanOn } from "./keys";

describe("where a key was pressed", () => {
  test("in a text field, whose keys are its own, or anywhere else", () => {
    expect(isTyping(document.createElement("input"))).toBe(true);
    expect(isTyping(document.createElement("textarea"))).toBe(true);
    expect(isTyping(document.createElement("button"))).toBe(false);
    expect(isTyping(window)).toBe(false);
    expect(isTyping(null)).toBe(false);
  });

  test("on a span's mark, which names the span, or on nothing that does", () => {
    const mark = document.createElement("span");
    mark.dataset.span = "s1";
    expect(spanOn(mark)).toBe("s1");
    expect(spanOn(document.createElement("span"))).toBeNull();
    expect(spanOn(window)).toBeNull();
    expect(spanOn(null)).toBeNull();
  });

  test("on a page, anything in it, which names its page, or on nothing that does", () => {
    const page = document.createElement("section");
    page.dataset.page = "2";
    const control = page.appendChild(document.createElement("button"));
    expect(pageOn(control)).toBe(2);
    expect(pageOn(page)).toBe(2);
    expect(pageOn(document.createElement("button"))).toBeNull();
    expect(pageOn(window)).toBeNull();
  });

  test("on a margin note, which says it is one, or on anything else", () => {
    const note = document.createElement("button");
    note.dataset.marginNote = "";
    expect(isOnMarginNote(note)).toBe(true);
    expect(isOnMarginNote(document.createElement("button"))).toBe(false);
    expect(isOnMarginNote(window)).toBe(false);
    expect(isOnMarginNote(null)).toBe(false);
  });
});

describe("a press on a slip", () => {
  /** Whether a press on `target` would still move focus: the browser's own default, not prevented. */
  function movesFocus(target: Element): boolean {
    const press = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    target.dispatchEvent(press);
    return !press.defaultPrevented;
  }

  test("leaves focus where it is, but goes into a field of the slip's own", () => {
    const slip = document.body.appendChild(document.createElement("div"));
    slip.addEventListener("mousedown", keepFocusOnPress);
    try {
      expect(movesFocus(slip)).toBe(false);
      expect(movesFocus(slip.appendChild(document.createElement("span")))).toBe(false);
      expect(movesFocus(slip.appendChild(document.createElement("input")))).toBe(true);
    } finally {
      slip.remove();
    }
  });
});

describe("where focus fell", () => {
  test("to nothing: the document, or a box that holds what lost it; anything else was somewhere chosen", () => {
    const pages = document.createElement("main");
    const field = pages.appendChild(document.createElement("input"));
    const elsewhere = pages.appendChild(document.createElement("button"));
    expect(fellToNothing({ target: field, relatedTarget: null })).toBe(true);
    expect(fellToNothing({ target: field, relatedTarget: pages })).toBe(true);
    expect(fellToNothing({ target: field, relatedTarget: elsewhere })).toBe(false);
  });
});
