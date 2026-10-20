import { describe, expect, test } from "vitest";
import { A4, aFit, aSpan } from "../../fixtures";
import { PX_PER_PT } from "../constants";
import { cropOf, fieldScaleOf, layerOf, markBoxOf, pinnedUnder, previewStyleOf, sheetWidthOf, topOf } from "./geometry";

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

describe("a span's mark", () => {
  test("runs as far as a replacement left long is drawn, so all of it can be pointed at and pressed", () => {
    const span = aSpan({ id: "s", bbox: { x0: 100, y0: 0, x1: 200, y1: 12 } });
    const longer = aFit({ delta_pt: 40 });
    expect(markBoxOf(span, { replaced: true, fit: longer })).toEqual({ x0: 100, y0: 0, x1: 240, y1: 12 });
    // A way out ends it where the original did; shorter, or put back, it keeps the original's place.
    expect(markBoxOf(span, { replaced: true, fit: aFit({ delta_pt: 40, strategy: "shrink" }) })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: true, fit: aFit({ delta_pt: -30 }) })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: false, fit: longer })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: true, fit: undefined })).toEqual(span.bbox);
  });
});

describe("what's set under a box", () => {
  test("is pinned at its left edge, or at its right edge in the page's right half, so it stays on the page", () => {
    const page = { width: 600 };
    // Just under the box, 56 pt down and the slip's gap more: 60 pt on a 600 pt page.
    expect(pinnedUnder({ x0: 60, x1: 200, y1: 56 }, page)).toEqual({ top: "10cqw", left: "10%" });
    // An amount in a right-hand column: what's under it ends where it does, not past the page.
    expect(pinnedUnder({ x0: 480, x1: 540, y1: 56 }, page)).toEqual({ top: "10cqw", right: "10%" });
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

describe("a page's sheet", () => {
  test("is as wide as the page is printed, turned or not", () => {
    expect(sheetWidthOf(A4)).toEqual({ "--sheet-width": `${595 * PX_PER_PT}px` });
    expect(sheetWidthOf({ ...A4, turn_cw: 90 })).toEqual({ "--sheet-width": `${842 * PX_PER_PT}px` });
  });

  test("holds the page in its own points, turned on its side as the file says", () => {
    expect(layerOf(A4)).toEqual({ width: "100%", height: "100%", transform: "translate(-50%, -50%) rotate(0deg)" });
    const turned = layerOf({ ...A4, turn_cw: 90 });
    expect(turned).toMatchObject({ width: `${(595 / 842) * 100}%`, transform: "translate(-50%, -50%) rotate(90deg)" });
  });

  test("places what's on it by its share of the page's height", () => {
    expect(topOf(421, A4)).toEqual({ top: "50%" });
  });
});

describe("a preview of a span's words", () => {
  test("sits in the span's place, a line as tall as the span, set at the way out's scale", () => {
    const span = aSpan({ id: "s", size: 10, bbox: { x0: 59.5, y0: 84.2, x1: 119, y1: 96.2 } });
    const style = previewStyleOf(span, A4, { face: "Liberation Serif Regular", scale: { size: 0.5, scaleX: 1 } });
    expect(style).toMatchObject({ left: "10%", top: "10%", minWidth: "10%", fontSize: `${(5 / 595) * 100}cqw` });
    expect(style.lineHeight).toBe(`${(12 / 595) * 100}cqw`);
    expect(style.transform).toBeUndefined();
    expect(style.letterSpacing).toBeUndefined();
  });

  test("sets its letters as far apart as the span's, in ems of the size it's set at", () => {
    const span = aSpan({ id: "s", letter_spacing_em: 0.05 });
    const style = previewStyleOf(span, A4, { face: "Liberation Serif Regular", scale: { size: 1, scaleX: 1 } });
    expect(style.letterSpacing).toBe("0.05em");
  });
});
