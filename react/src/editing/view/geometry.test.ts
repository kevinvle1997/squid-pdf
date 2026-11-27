import { describe, expect, test } from "vitest";
import { A4, aFit, aSpan } from "../../fixtures";
import {
  FIELD_SPARE_EM,
  HANDWRITING_STEP,
  NEW_TEXT_ASCENT_EM,
  NEW_TEXT_DESCENT_EM,
  NEW_TEXT_FIELD_MIN_EM,
  PX_PER_PT,
  SLIP_GAP_PT,
  STRIP_PAD_PT,
} from "../constants";
import {
  cropOf,
  fieldScaleOf,
  layerOf,
  markBoxOf,
  newTextFieldWidthPt,
  newTextSlipLineOf,
  paddedBoxOf,
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
    expect(markBoxOf(span, { replaced: true, fit: aFit({ delta_pt: 40, strategy: "shrink" }) })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: true, fit: aFit({ delta_pt: -30 }) })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: false, fit: longer })).toEqual(span.bbox);
    expect(markBoxOf(span, { replaced: true, fit: undefined })).toEqual(span.bbox);
  });
});

const TALL = { top: Infinity, bottom: Infinity, pagesStart: Infinity, pagesEnd: Infinity, zoomControl: null };

function placed(left: string, top: string) {
  return { left: 0, top: 0, translate: `${left} ${top}` };
}

function placedOver(left: string, foot: string) {
  return { left: 0, bottom: 0, translate: `${left} ${foot}` };
}

function placedLeftOf(right: string, top: string) {
  return { right: 0, top: 0, translate: `${right} ${top}` };
}

describe("what's set under a box", () => {
  test("is pinned at its left edge, or at its right edge in the page's right half, so it stays on the page", () => {
    const page = { width: 600 };
    expect(pinnedUnder({ x0: 60, x1: 200, y1: 56 }, page)).toEqual({ top: "10cqw", left: "10%" });
    expect(pinnedUnder({ x0: 480, x1: 540, y1: 56 }, page)).toEqual({ top: "10cqw", right: "10%" });
  });

  test("wider than its page, keeps to the window, the gutter in from its edge, where it can", () => {
    const small = { width: 200, height: 120, turn_cw: 0 } as const;
    const line = { x0: 20, y0: 10, x1: 80, y1: 20 };
    const size = { width: 230, height: 60 };
    expect(slipPlaceOf(line, small, { size, room: { ...TALL, left: 30, right: 6 } })).toEqual(
      placed(points(-24, small), points(24, small)),
    );
    expect(slipPlaceOf(line, small, { size, room: { ...TALL, left: 4, right: 6 } })).toEqual(
      placed(points(-4, small), points(24, small)),
    );
  });

  test("once its size is known, moves in from a side of the page it would run past, along under its line", () => {
    const left = { x0: 60, y0: 10, x1: 200, y1: 20 };
    expect(slipPlaceOf(left, A4, { size: { width: 580, height: 60 } })).toEqual(placed(points(15, A4), points(24, A4)));
    const right = { x0: 320, y0: 10, x1: 400, y1: 20 };
    expect(slipPlaceOf(right, A4, { size: { width: 450, height: 60 } })).toEqual(placed(points(0, A4), points(24, A4)));
  });
});

describe("a slip under a line near the window's foot", () => {
  const line = { x0: 72, y0: 700, x1: 152, y1: 720 };
  const size = { width: 175, height: 80 };
  const room = { ...TALL, left: 0, right: 0, top: -100, bottom: 760 - 842 };

  test("hangs above its line, grown up from it, when the window has no room below and has it above", () => {
    expect(slipSideOf(line, A4, { size, room })).toBe("above");
    expect(slipPlaceOf(line, A4, { size, side: "above", room })).toEqual(
      placedOver(points(72, A4), points(700 - SLIP_GAP_PT - 842, A4)),
    );
  });

  test("wider than its page, as on a phone, hangs above its line too", () => {
    expect(slipSideOf(line, A4, { size: { width: 700, height: 80 }, room })).toBe("above");
  });

  test("stays under its line while the window has room there, so nothing moves for it", () => {
    expect(slipSideOf(line, A4, { size, room: { ...room, bottom: 0 } })).toBe("below");
  });

  test("with room on neither side, stays under its line", () => {
    expect(slipSideOf(line, A4, { size, room: { ...room, top: -680 } })).toBe("below");
  });

  test("where the zoom control lies under it, hangs above its line when under it would reach the control", () => {
    const zoomed = { ...room, bottom: 820 - 842, zoomControl: { left: 200, bottom: 790 - 842 } };
    expect(slipSideOf(line, A4, { size, room: zoomed })).toBe("above");
    expect(slipSideOf(line, A4, { size, room: { ...zoomed, zoomControl: { left: 300, bottom: -52 } } })).toBe("below");
  });

  test("near its page's top, hangs above it over the page before when the window has room there", () => {
    const nearTop = { x0: 72, y0: 30, x1: 152, y1: 50 };
    const seen = { ...room, top: 300, bottom: 100 - 842 };
    expect(slipSideOf(nearTop, A4, { size, room: seen })).toBe("above");
    expect(slipSideOf(nearTop, A4, { size, room: { ...seen, top: 20, pagesStart: 20 } })).toBe("below");
  });

  test("with room on neither side, near the pages' end, hangs above its line, which a scroll can show", () => {
    const lastPage = { ...room, top: -680, bottom: 60, pagesEnd: 60 };
    expect(slipSideOf(line, A4, { size: { width: 175, height: 200 }, room: lastPage })).toBe("above");
  });

  test("held under its line, stays there as it grows past the window's foot, which a scroll can show", () => {
    expect(slipSideOf(line, A4, { size, room, held: "below" })).toBe("below");
  });

  test("held under its line, hangs above it once it grows past the pages' end, which no scroll shows", () => {
    const calendar = { width: 175, height: 300 };
    const lastPage = { ...room, top: -400, bottom: 60, pagesEnd: 60 };
    expect(slipSideOf(line, A4, { size: { width: 175, height: 40 }, room: lastPage, held: "below" })).toBe("below");
    expect(slipSideOf(line, A4, { size: calendar, room: lastPage, held: "below" })).toBe("above");
  });

  test("held over its line once under it passed the pages' end, stays there as it shrinks", () => {
    const lastPage = { ...room, top: -400, bottom: 60, pagesEnd: 60 };
    expect(slipSideOf(line, A4, { size: { width: 175, height: 40 }, room: lastPage, held: "above" })).toBe("above");
  });

  test("on a page turned a quarter, goes before the line's start when beside it would pass the window's foot", () => {
    const nearFoot = { x0: 72, y0: 800, x1: 152, y1: 820 };
    const turned = { ...A4, turn_cw: 90 } as const;
    const short = { ...TALL, left: 0, right: 0, top: 0, bottom: 120 - 595 };
    expect(slipSideOf(nearFoot, turned, { size: { width: 175, height: 60 }, room: short })).toBe("above");
  });
});

describe("a slip on a turned page", () => {
  const line = { x0: 40, y0: 10, x1: 140, y1: 20 };
  const under = 20 + SLIP_GAP_PT;

  test("is set under its line, unturned, by the corner that keeps it off the line as the line is shown", () => {
    expect(slipPlaceOf(line, { ...A4, turn_cw: 90 })).toEqual({
      top: points(40, { width: 842 }),
      right: `${(under / 842) * 100}%`,
    });
    expect(slipPlaceOf(line, { ...A4, turn_cw: 180 })).toEqual({
      bottom: `${(under / 842) * 100}%`,
      right: `${(40 / 595) * 100}%`,
    });
    expect(slipPlaceOf(line, { ...A4, turn_cw: 270 })).toEqual({
      bottom: `${(40 / 595) * 100}%`,
      left: `${(under / 842) * 100}%`,
    });
  });

  describe("once its size is known, a line near the page's foot", () => {
    const nearFoot = { x0: 72, y0: 800, x1: 152, y1: 820 };
    const size = { width: 175, height: 60 };
    const across = { width: 842 };

    test("turned a quarter clockwise, with no room left of it, has its slip right of it, level with its start", () => {
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size })).toEqual(
        placed(points(42 + SLIP_GAP_PT, across), points(72, across)),
      );
    });

    test("turned a quarter the other way, with no room right of it, has its slip left of it, level with its start", () => {
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 270 }, { size })).toEqual(
        placedLeftOf(points(800 - SLIP_GAP_PT - 842, across), points(523 - 60, across)),
      );
    });

    test("upside down, with no room above it, has its slip below it, from its start", () => {
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 180 }, { size })).toEqual(
        placed(points(523 - 175, A4), points(42 + SLIP_GAP_PT, A4)),
      );
    });

    test("with no room either side, has its slip before its start, else past its end, never over it", () => {
      const wide = { width: 830, height: 60 };
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size: wide })).toEqual(
        placedOver(points(0, across), points(72 - SLIP_GAP_PT - 595, across)),
      );
      const tall = { width: 830, height: 100 };
      expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 90 }, { size: tall, endPt: 300 })).toEqual(
        placed(points(0, across), points(300 + SLIP_GAP_PT, across)),
      );
    });
  });

  test("turned a quarter the other way, with no room either side, has its slip before its start, never past its end", () => {
    const nearFoot = { x0: 72, y0: 800, x1: 152, y1: 820 };
    const wide = { width: 830, height: 60 };
    expect(slipPlaceOf(nearFoot, { ...A4, turn_cw: 270 }, { size: wide })).toEqual(
      placed(points(842 - 830, { width: 842 }), points(523 + SLIP_GAP_PT, { width: 842 })),
    );
  });

  test("kept on the side it's held on, though the side under its line has room", () => {
    const size = { width: 175, height: 60 };
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

describe("new text's place", () => {
  test("its field is as wide as it draws, never narrower than a couple of letters, with room for the caret", () => {
    expect(newTextFieldWidthPt({ size: 10 }, 100)).toBe(100 + 10 * FIELD_SPARE_EM);
    expect(newTextFieldWidthPt({ size: 10 }, 0)).toBe(10 * NEW_TEXT_FIELD_MIN_EM + 10 * FIELD_SPARE_EM);
  });

  test("its slip hangs under the line as a handwriting face would set it, so choosing one doesn't move it", () => {
    const line = newTextSlipLineOf({ origin: [72, 400], size: 10 }, { nearSize: 10, fieldWidthPt: 50 });
    const handwritten = 10 * HANDWRITING_STEP;
    expect(line).toEqual({
      x0: 72,
      y0: 400 - handwritten * NEW_TEXT_ASCENT_EM,
      x1: 122,
      y1: 400 + handwritten * NEW_TEXT_DESCENT_EM,
    });
  });

  test("what the server redraws round it reaches as far as a strip does, and stops at the page's edges", () => {
    const padded = paddedBoxOf({ x0: 1, y0: 100, x1: A4.width - 1, y1: 120 }, A4);
    expect(padded).toEqual({ x0: 0, y0: 100 - STRIP_PAD_PT, x1: A4.width, y1: 120 + STRIP_PAD_PT });
  });
});
