import { describe, expect, test } from "vitest";
import { aDoc } from "../fixtures";
import { scaleFor, whereSentence, zoomStep } from "./zoom";

describe("a zoom step", () => {
  test("in goes to the next share worth the press: from a phone's fit, three quarters, not a half", () => {
    expect(zoomStep(0.45, 1)).toBe(0.75);
    expect(zoomStep(1, 1)).toBe(1.25);
    expect(zoomStep(1.5, 1)).toBe(2);
  });

  test("out goes to the share before, and never past either end, nor the other way", () => {
    expect(zoomStep(1, -1)).toBe(0.75);
    expect(zoomStep(0.45, -1)).toBe(0.45);
    expect(zoomStep(3, 1)).toBe(3);
  });
});

test("zoomed in, pages are drawn sharp enough for it, and never blurrier than they were", () => {
  // At 2x, a page shown at its printed size needs 2 * 4/3 pixels a point, rounded up.
  expect(scaleFor(1, { pixelRatio: 2, scale: 1 })).toBe(3);
  expect(scaleFor(2, { pixelRatio: 1, scale: 2 })).toBe(3);
  expect(scaleFor(0.5, { pixelRatio: 1, scale: 2 })).toBe(2);
  expect(scaleFor(3, { pixelRatio: 2, scale: 1 })).toBe(4);
});

test("the bar says which page is being read, or a one-page document's length", () => {
  const two = aDoc({
    pages: [
      { width: 595, height: 842, turn_cw: 0 },
      { width: 595, height: 842, turn_cw: 0 },
    ],
  });
  expect(whereSentence({ doc: two, pageInView: 1 })).toBe("Page 2 of 2");
  const one = aDoc({ pages: [{ width: 595, height: 842, turn_cw: 0 }] });
  expect(whereSentence({ doc: one, pageInView: 0 })).toBe("1 page");
});
