import { describe, expect, test } from "vitest";
import type { PageInfo, SpanInfo } from "../api/types";
import { merged, regionsFor, rowOf } from "./strips";

const page: PageInfo = { width: 595, height: 842, rotation: 0 };
const spanAt = (y0: number, y1: number): SpanInfo => ({
  id: `s${y0}`,
  page: 0,
  text: "x",
  font: "f",
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 72, y0, x1: 100, y1 },
  origin: [72, y1 - 2],
  fidelity: "exact",
});

describe("strips to redraw", () => {
  test("a strip reaches just past its span, and never past the page", () => {
    expect(rowOf(spanAt(100, 112), page)).toEqual({ y0: 98, y1: 114 });
    expect(rowOf(spanAt(0, 12), page).y0).toBe(0);
    expect(rowOf(spanAt(835, 842), page).y1).toBe(842);
  });

  test("two edits on one line are one strip", () => {
    expect(merged([{ y0: 98, y1: 114 }, { y0: 99, y1: 113 }])).toEqual([{ y0: 98, y1: 114 }]);
  });

  test("edits on separate lines are separate strips, top first", () => {
    const regions = regionsFor(2, page, [spanAt(300, 312), spanAt(100, 112)]);
    expect(regions).toEqual([
      { page: 2, y0: 98, y1: 114 },
      { page: 2, y0: 298, y1: 314 },
    ]);
  });
});
