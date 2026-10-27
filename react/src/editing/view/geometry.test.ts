import { describe, expect, test } from "vitest";
import { A4, aFit, aSpan } from "../../fixtures";
import { PX_PER_PT, SLIP_GAP_PT } from "../constants";
import {
  cropOf,
  fieldScaleOf,
  layerOf,
  markBoxOf,
  pinnedUnder,
  points,
  previewStyleOf,
  sheetWidthOf,
  slipPlaceOf,
  slipSideOf,
  topOf,
  uprightOf,
} from "./geometry";

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

/** A slip's place once its size is known: moved from its layer's corner by `left` and `top`. */
function placed(left: string, top: string) {
  return { left: 0, top: 0, translate: `${left} ${top}` };
}

/** A slip's place over its line: moved from its layer's foot, so it grows up, away from the line. */
function placedOver(left: string, foot: string) {
  return { left: 0, bottom: 0, translate: `${left} ${foot}` };
}

/** A slip's place left of its line: moved from its layer's right edge, so it grows left, away from the line. */
function placedLeftOf(right: string, top: string) {
  return { right: 0, top: 0, translate: `${right} ${top}` };
}

describe("what's set under a box", () => {
  test("is pinned at its left edge, or at its right edge in the page's right half, so it stays on the page", () => {
    const page = { width: 600 };
    // Just under the box, 56 pt down and the slip's gap more: 60 pt on a 600 pt page.
    expect(pinnedUnder({ x0: 60, x1: 200, y1: 56 }, page)).toEqual({ top: "10cqw", left: "10%" });
    // An amount in a right-hand column: what's under it ends where it does, not past the page.
    expect(pinnedUnder({ x0: 480, x1: 540, y1: 56 }, page)).toEqual({ top: "10cqw", right: "10%" });
  });

  test("wider than its page, keeps to the window, the gutter in from its edge, where it can", () => {
    // A 200 pt page with 30 pt of the window spare on its left and 6 pt on its right, and a slip 230 pt wide.
    const small = { width: 200, height: 120, turn_cw: 0 } as const;
    const line = { x0: 20, y0: 10, x1: 80, y1: 20 };
    const size = { width: 230, height: 60 };
    expect(slipPlaceOf(line, small, { size, room: { left: 30, right: 6 } })).toEqual(
      placed(points(-24, small), points(24, small)),
    );
    // With less spare than it needs, it starts at the gutter, and runs past the page's right edge.
    expect(slipPlaceOf(line, small, { size, room: { left: 4, right: 6 } })).toEqual(
      placed(points(-4, small), points(24, small)),
    );
  });

  test("once its size is known, moves in from a side of the page it would run past, along under its line", () => {
    // A slip 580 pt wide under a line 60 pt in: moved left until it ends where the page does.
    const left = { x0: 60, y0: 10, x1: 200, y1: 20 };
    expect(slipPlaceOf(left, A4, { size: { width: 580, height: 60 } })).toEqual(placed(points(15, A4), points(24, A4)));
    // Pinned at its right edge, in the page's right half: moved right until it starts where the page does.
    const right = { x0: 320, y0: 10, x1: 400, y1: 20 };
    expect(slipPlaceOf(right, A4, { size: { width: 450, height: 60 } })).toEqual(placed(points(0, A4), points(24, A4)));
  });
});

describe("a slip on a turned page", () => {
  // A line 100 pt long at the top left of an A4 page, its foot 20 pt down: under it, the slip's gap more.
  const line = { x0: 40, y0: 10, x1: 140, y1: 20 };
  const under = 20 + SLIP_GAP_PT;

  test("is set under its line, unturned, by the corner that keeps it off the line as the line is shown", () => {
    // Turned a quarter clockwise, the line runs down the screen from 40 pt, at the right: under it is to its left.
    expect(slipPlaceOf(line, { ...A4, turn_cw: 90 })).toEqual({
      top: points(40, { width: 842 }),
      right: `${(under / 842) * 100}%`,
    });
    // Upside down, it runs left from the right, near the foot: under it is above it.
    expect(slipPlaceOf(line, { ...A4, turn_cw: 180 })).toEqual({
      bottom: `${(under / 842) * 100}%`,
      right: `${(40 / 595) * 100}%`,
    });
    // Turned a quarter the other way, it runs up the screen from the foot, at the left: under it is to its right.
    expect(slipPlaceOf(line, { ...A4, turn_cw: 270 })).toEqual({
      bottom: `${(40 / 595) * 100}%`,
      left: `${(under / 842) * 100}%`,
    });
  });

  describe("once its size is known, a line near the page's foot", () => {
    // 20 pt tall, 80 pt long, 22 pt from the foot: by a side of the screen turned a quarter, at its top turned a half.
    const nearFoot = { x0: 72, y0: 800, x1: 152, y1: 820 };
    const size = { width: 175, height: 60 };
    const across = { width: 842 };

    test("turned a quarter clockwise, with no room left of it, has its slip right of it, level with its start", () => {
      // Shown 22 to 42 pt from the screen's left, from 72 to 152 pt down it.
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size })).toEqual(
        placed(points(42 + SLIP_GAP_PT, across), points(72, across)),
      );
    });

    test("turned a quarter the other way, with no room right of it, has its slip left of it, level with its start", () => {
      // Shown 800 to 820 pt from the left, its start 523 pt down, at its foot.
      // Hung by its right edge, which faces the line, the gap before the line's 800 pt across the 842 pt shown wide.
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 270 }, { size })).toEqual(
        placedLeftOf(points(800 - SLIP_GAP_PT - 842, across), points(523 - 60, across)),
      );
    });

    test("upside down, with no room above it, has its slip below it, from its start", () => {
      // Shown 22 to 42 pt down, its start 523 pt from the left, at its right end.
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 180 }, { size })).toEqual(
        placed(points(523 - 175, A4), points(42 + SLIP_GAP_PT, A4)),
      );
    });

    test("with no room either side, has its slip before its start, else past its end, never over it", () => {
      const wide = { width: 830, height: 60 };
      // Its foot the gap before the line's start, 72 pt down the 595 pt the page shows tall.
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size: wide })).toEqual(
        placedOver(points(0, across), points(72 - SLIP_GAP_PT - 595, across)),
      );
      // Past its end as the field draws it, typed on to 300 pt.
      const tall = { width: 830, height: 100 };
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size: tall, endPt: 300 })).toEqual(
        placed(points(0, across), points(300 + SLIP_GAP_PT, across)),
      );
    });
  });

  test("turned a quarter the other way, with no room either side, has its slip before its start, never past its end", () => {
    // The line runs up the screen from 523 pt down: before its start is below it, and past its end, where the
    // field grows, is above it, where this slip would have room.
    const nearFoot = { x0: 72, y0: 800, x1: 152, y1: 820 };
    const wide = { width: 830, height: 60 };
    expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 270 }, { size: wide })).toEqual(
      placed(points(842 - 830, { width: 842 }), points(523 + SLIP_GAP_PT, { width: 842 })),
    );
  });

  test("kept on the side it's held on, though the side under its line has room", () => {
    const size = { width: 175, height: 60 };
    // Upside down, a line low on the page has room over it, on screen, but the slip is held under it.
    const low = { x0: 72, y0: 400, x1: 152, y1: 420 };
    expect(slipSideOf(low, { ...A4, turn_cw: 180 }, { size })).toBe("above");
    expect(slipPlaceOf(low, { ...A4, turn_cw: 180 }, { size, side: "below" })).toEqual(
      placed(points(523 - 175, A4), points(842 - 400 + SLIP_GAP_PT, A4)),
    );
  });

  test("hangs from a layer the page's shape as it's shown, upright", () => {
    expect(uprightOf({ ...A4, turn_cw: 90 })).toEqual({ width: 842, height: 595, turn_cw: 0 });
    expect(uprightOf(A4)).toEqual(A4);
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
