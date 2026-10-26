// A view test though nothing's drawn: what a key was pressed on is an element, which only a browser has.
import { describe, expect, test } from "vitest";
import { isTyping, pageOn, spanOn } from "./keys";

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
});
