import { describe, expect, test } from "vitest";
import { cropOf, fieldScaleOf, pinnedUnder } from "./geometry";

describe("a touch screen's field", () => {
  test("scales from the size it's set at down to the span's, as the page is shown", () => {
    // 10 pt on a 600 pt page shown 960 px wide is 16 px: set at 16 px, it stays as it is.
    expect(fieldScaleOf(10, { pageWidthPt: 600, shownPx: 960, fontPx: 16 })).toBe(1);
    expect(fieldScaleOf(10, { pageWidthPt: 600, shownPx: 480, fontPx: 16 })).toBe(0.5);
  });

  test("a size not worked out yet leaves the field as it is", () => {
    expect(fieldScaleOf(10, { pageWidthPt: 600, shownPx: 480, fontPx: 0 })).toBe(1);
    expect(fieldScaleOf(10, { pageWidthPt: 600, shownPx: 0, fontPx: 16 })).toBe(1);
  });
});

describe("what's set under a box", () => {
  test("is pinned at its left edge, or at its right edge in the page's right half, so it stays on the page", () => {
    const page = { width: 600 };
    expect(pinnedUnder({ x0: 60, x1: 200 }, page)).toEqual({ left: "10%" });
    // An amount in a right-hand column: what's under it ends where it does, not past the page.
    expect(pinnedUnder({ x0: 480, x1: 540 }, page)).toEqual({ right: "10%" });
  });
});

describe("a box showing the page's own pixels", () => {
  test("holds the page's image as wide as the page is to the box, moved by where the box starts", () => {
    // A 100 by 20 point box at (50, 200) on a 600 point page: the image is six boxes wide, half a box left, ten up.
    expect(cropOf({ x0: 50, y0: 200, x1: 150, y1: 220 }, { width: 600 })).toEqual({
      width: "600%",
      left: "-50%",
      top: "-1000%",
    });
  });
});
