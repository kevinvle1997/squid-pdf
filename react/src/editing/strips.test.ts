import { describe, expect, test } from "vitest";
import type { Edit, Insert, PageInfo, SpanInfo } from "../api/types";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { UNEDITED, project } from "./project";
import { insertRowOf, merged, regionsFor, rowOf, stalePages } from "./strips";

const page: PageInfo = { width: 595, height: 842, rotation: 0 };
const spanAt = (y0: number, y1: number, pageIndex = 0): SpanInfo => ({
  id: `s${pageIndex}-${y0}`,
  page: pageIndex,
  text: "x",
  font: "f",
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 72, y0, x1: 100, y1 },
  origin: [72, y1 - 2],
  fidelity: "exact",
});
const SPANS = [spanAt(100, 112), spanAt(300, 312), spanAt(100, 112, 1)];
const readingOf = (...edits: Edit[]) =>
  project(SPANS, entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));
const replace = (span: SpanInfo, text = "y"): Edit => ({ kind: "replace", span_id: span.id, text });

describe("strips to redraw", () => {
  test("a strip reaches just past its span, and never past the page", () => {
    expect(rowOf(spanAt(100, 112), page)).toEqual({ y0: 98, y1: 114 });
    expect(rowOf(spanAt(0, 12), page).y0).toBe(0);
    expect(rowOf(spanAt(835, 842), page).y1).toBe(842);
  });

  test("new text's strip is a line tall around its baseline", () => {
    const insert: Insert = { kind: "insert", page: 0, origin: [72, 400], text: "Signed", size: 10 };
    expect(insertRowOf(insert, page)).toEqual({ y0: 388, y1: 405 });
  });

  test("two edits on one line are one strip", () => {
    expect(merged([{ y0: 98, y1: 114 }, { y0: 99, y1: 113 }])).toEqual([{ y0: 98, y1: 114 }]);
  });

  test("edits on separate lines are separate strips, top first", () => {
    const [top, bottom] = SPANS as [SpanInfo, SpanInfo];
    const view = readingOf(replace(bottom), replace(top));
    expect(regionsFor(0, page, view.pages.get(0)!)).toEqual([
      { page: 0, y0: 98, y1: 114 },
      { page: 0, y0: 298, y1: 314 },
    ]);
  });
});

describe("pages to redraw", () => {
  const [top, , other] = SPANS as [SpanInfo, SpanInfo, SpanInfo];

  test("a page is stale when its edits read other than what its strips were drawn from", () => {
    const view = readingOf(replace(top), replace(other));
    expect(stalePages(view, new Map())).toEqual(new Set([0, 1]));
    expect(stalePages(view, new Map([[0, view]]))).toEqual(new Set([1]));
  });

  test("a page whose strips show edits since undone is stale, until it's drawn bare", () => {
    const drawn = readingOf(replace(top));
    expect(stalePages(UNEDITED, new Map([[0, drawn]]))).toEqual(new Set([0]));
    expect(stalePages(UNEDITED, new Map([[0, UNEDITED]]))).toEqual(new Set());
  });

  test("a failed render leaves its page stale, so the next asks for it again", () => {
    const first = readingOf(replace(top));
    // The render of `first` never landed; now page 1 is edited too.
    const next = readingOf(replace(top), replace(other));
    expect(stalePages(next, new Map([[1, first]]))).toEqual(new Set([0, 1]));
  });
});
