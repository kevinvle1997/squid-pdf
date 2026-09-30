// Which strips of a page to redraw. Edits stay in their row, so a full-width strip
// around each changed line is byte-identical to the page and a few KB, not a page.
import type { Insert, PageInfo, Region, SpanInfo } from "../api/types";
import { INSERT_ASCENT_EM, INSERT_DESCENT_EM, STRIP_PAD_PT } from "./constants";
import { type EditedView, type PageEdits, samePage } from "./project";

export interface Row {
  y0: number;
  y1: number;
}

function within(page: PageInfo, y0: number, y1: number): Row {
  return { y0: Math.max(0, y0 - STRIP_PAD_PT), y1: Math.min(page.height, y1 + STRIP_PAD_PT) };
}

/** The strip a span's edit is drawn in, in points, inside the page. */
export function rowOf(span: SpanInfo, page: PageInfo): Row {
  return within(page, span.bbox.y0, span.bbox.y1);
}

/** The strip a new line is drawn in, from its baseline. */
export function insertRowOf(insert: Insert, page: PageInfo): Row {
  const [, baseline] = insert.origin;
  return within(page, baseline - insert.size * INSERT_ASCENT_EM, baseline + insert.size * INSERT_DESCENT_EM);
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
export function regionsFor(pageIndex: number, page: PageInfo, edits: PageEdits): Region[] {
  const rows = [
    ...edits.spans.map((view) => rowOf(view.span, page)),
    ...edits.inserts.map((insert) => insertRowOf(insert.edit, page)),
  ];
  return merged(rows).map((row) => ({ page: pageIndex, ...row }));
}

/** The pages whose edits now read other than the reading their strips were drawn from. */
export function stalePages(view: EditedView, drawn: ReadonlyMap<number, EditedView>): Set<number> {
  const pages = new Set([...view.pages.keys(), ...drawn.keys()]);
  return new Set([...pages].filter((page) => !samePage(view.pages.get(page), drawn.get(page)?.pages.get(page))));
}
