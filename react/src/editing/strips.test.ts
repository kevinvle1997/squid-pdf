import { describe, expect, test } from "vitest";
import type { Edit, SpanInfo } from "../api/types";
import { A4, anInsert, aRedact, aReplace, aSpan } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { project, UNEDITED } from "./project";
import {
  bottomOf,
  insertRowOf,
  isUnderStrip,
  merged,
  regionsFor,
  rowOf,
  stalePages,
  stillShows,
  stripBoxOf,
} from "./strips";

const spanAt = (y0: number, y1: number, page = 0): SpanInfo =>
  aSpan({ id: `s${page}-${y0}`, page, bbox: { x0: 72, y0, x1: 100, y1 } });
const SPANS = [spanAt(100, 112), spanAt(300, 312), spanAt(100, 112, 1)];
const readingOf = (...edits: Edit[]) =>
  project(SPANS, entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));

describe("strips to redraw", () => {
  test("a strip reaches just past its span, and never past the page", () => {
    expect(rowOf(spanAt(100, 112), A4)).toEqual({ y0: 98, y1: 114 });
    expect(rowOf(spanAt(0, 12), A4).y0).toBe(0);
    expect(rowOf(spanAt(835, 842), A4).y1).toBe(842);
  });

  test("new text's strip is a line tall around its baseline", () => {
    const insert = anInsert({ origin: [72, 400], size: 10 });
    expect(insertRowOf(insert, A4)).toEqual({ y0: 388, y1: 405 });
  });

  test("two edits on one line are one strip", () => {
    expect(
      merged([
        { y0: 98, y1: 114 },
        { y0: 99, y1: 113 },
      ]),
    ).toEqual([{ y0: 98, y1: 114 }]);
  });

  test("edits on separate lines are separate strips, top first", () => {
    const [top, bottom] = SPANS as [SpanInfo, SpanInfo];
    const reading = readingOf(aReplace(bottom.id), aReplace(top.id));
    expect(regionsFor(0, A4, reading.pages.get(0) ?? { spans: [], inserts: [] })).toEqual([
      { page: 0, y0: 98, y1: 114 },
      { page: 0, y0: 298, y1: 314 },
    ]);
  });
});

describe("pages to redraw", () => {
  const [top, , other] = SPANS as [SpanInfo, SpanInfo, SpanInfo];

  test("a page is stale when its edits read other than what its strips were drawn from", () => {
    const reading = readingOf(aReplace(top.id), aReplace(other.id));
    expect(stalePages(reading, new Map())).toEqual(new Set([0, 1]));
    expect(stalePages(reading, new Map([[0, reading]]))).toEqual(new Set([1]));
  });

  test("a page whose strips show edits since undone is stale, until it's drawn bare", () => {
    const drawn = readingOf(aReplace(top.id));
    expect(stalePages(UNEDITED, new Map([[0, drawn]]))).toEqual(new Set([0]));
    expect(stalePages(UNEDITED, new Map([[0, UNEDITED]]))).toEqual(new Set());
  });

  test("a failed render leaves its page stale, so the next asks for it again", () => {
    const first = readingOf(aReplace(top.id));
    // The render of `first` never landed; now page 1 is edited too.
    const next = readingOf(aReplace(top.id), aReplace(other.id));
    expect(stalePages(next, new Map([[1, first]]))).toEqual(new Set([0, 1]));
  });
});

describe("a strip the server drew", () => {
  test("ends where the region it was asked for ends, though its top was rounded to a pixel row", () => {
    const regions = [
      { page: 0, y0: 98, y1: 114 },
      { page: 0, y0: 298, y1: 314 },
      { page: 1, y0: 98 },
    ];
    expect(bottomOf({ page: 0, y: 297.75 }, regions, A4)).toBe(314);
    expect(bottomOf({ page: 0, y: 98 }, regions, A4)).toBe(114);
    // A region with no bottom is the rest of the page.
    expect(bottomOf({ page: 1, y: 98 }, regions, A4)).toBe(A4.height);
  });

  test("still shows an edit while one is in its rows; once none is, the page beneath is right as it is", () => {
    const [top, bottom] = SPANS as [SpanInfo, SpanInfo];
    const strip = { y: 98, y1: 114 };
    expect(stillShows(strip, { edits: readingOf(aReplace(top.id), aReplace(bottom.id)).pages.get(0), info: A4 })).toBe(
      true,
    );
    expect(stillShows(strip, { edits: readingOf(aReplace(bottom.id)).pages.get(0), info: A4 })).toBe(false);
    expect(stillShows(strip, { edits: readingOf(aRedact(top.id)).pages.get(0), info: A4 })).toBe(true);
    expect(stillShows(strip, { edits: undefined, info: A4 })).toBe(false);
  });

  test("covers a span when it crosses the span's row, and reaches as far across it as it does down", () => {
    const span = spanAt(100, 112);
    expect(isUnderStrip(span, { strips: [{ y: 98, y1: 114 }], info: A4 })).toBe(true);
    expect(isUnderStrip(span, { strips: [{ y: 298, y1: 314 }], info: A4 })).toBe(false);
    expect(isUnderStrip(span, { strips: undefined, info: A4 })).toBe(false);
    expect(stripBoxOf(span, A4)).toEqual({ x0: 70, y0: 98, x1: 102, y1: 114 });
    expect(stripBoxOf(aSpan({ id: "wide", bbox: { x0: 1, y0: 100, x1: A4.width, y1: 112 } }), A4)).toMatchObject({
      x0: 0,
      x1: A4.width,
    });
  });
});
