import { describe, expect, test } from "vitest";
import { fieldScaleOf } from "./geometry";

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
