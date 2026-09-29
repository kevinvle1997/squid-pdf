// Which strips of a page to redraw. Edits stay in their row, so a full-width strip
// around each changed span is byte-identical to the page and a few KB, not a page.
import type { PageInfo, Region, SpanInfo } from "../api/types";
import { STRIP_PAD_PT } from "./constants";

export interface Row {
  y0: number;
  y1: number;
}

/** The strip a span's edit is drawn in, in points, inside the page. */
export function rowOf(span: SpanInfo, page: PageInfo): Row {
  return {
    y0: Math.max(0, span.bbox.y0 - STRIP_PAD_PT),
    y1: Math.min(page.height, span.bbox.y1 + STRIP_PAD_PT),
  };
}

/** Rows that overlap or touch become one, so no strip is drawn twice. */
export function merged(rows: readonly Row[]): Row[] {
  const sorted = [...rows].sort((a, b) => a.y0 - b.y0);
  const out: Row[] = [];
  for (const row of sorted) {
    const last = out.at(-1);
    if (last !== undefined && row.y0 <= last.y1) last.y1 = Math.max(last.y1, row.y1);
    else out.push({ ...row });
  }
  return out;
}

/** The regions a render of this page asks for: one strip per group of changed rows. */
export function regionsFor(pageIndex: number, page: PageInfo, spans: readonly SpanInfo[]): Region[] {
  return merged(spans.map((span) => rowOf(span, page))).map((row) => ({ page: pageIndex, ...row }));
}
