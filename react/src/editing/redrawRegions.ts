// Which regions of a page to redraw, each a full-width strip: edits stay in their row, so a strip
// around each changed line is byte-identical to the page around it.
import type { Insert, PageInfo, Region, SpanInfo } from "../api/types";
import { NEW_TEXT_ASCENT_EM, NEW_TEXT_DESCENT_EM, STRIP_PAD_PT } from "./constants";
import { type PageEdits, type Reading, samePage } from "./project";

export interface Row {
  y0: number;
  y1: number;
}

/** The row from `y0` to `y1`, as far past either as a strip reaches, inside the page. */
function rowWithin(info: PageInfo, y0: number, y1: number): Row {
  return { y0: Math.max(0, y0 - STRIP_PAD_PT), y1: Math.min(info.height, y1 + STRIP_PAD_PT) };
}

/** The strip a span's edit is drawn in, in points, inside the page. */
export function rowOf(span: SpanInfo, info: PageInfo): Row {
  return rowWithin(info, span.bbox.y0, span.bbox.y1);
}

/** The strip a new line is drawn in, from its baseline. */
export function insertRowOf(insert: Insert, info: PageInfo): Row {
  const [, baseline] = insert.origin;
  return rowWithin(info, baseline - insert.size * NEW_TEXT_ASCENT_EM, baseline + insert.size * NEW_TEXT_DESCENT_EM);
}

/** What the server would redraw of a span's row: across the span, as far past either end as a strip reaches. */
export function stripBoxOf(span: SpanInfo, info: PageInfo): { x0: number; y0: number; x1: number; y1: number } {
  const { y0, y1 } = rowOf(span, info);
  return {
    x0: Math.max(0, span.bbox.x0 - STRIP_PAD_PT),
    y0,
    x1: Math.min(info.width, span.bbox.x1 + STRIP_PAD_PT),
    y1,
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
export function regionsFor(page: number, info: PageInfo, edits: PageEdits): Region[] {
  const rows = [
    ...edits.spans.map((reading) => rowOf(reading.span, info)),
    ...edits.newTexts.map((insert) => insertRowOf(insert.edit, info)),
  ];
  return merged(rows).map((row) => ({ page, ...row }));
}

/** Where a strip the server drew ends, in points: the bottom of the region asked for that it tops, the nearest. */
export function bottomOf(image: { page: number; y: number }, regions: readonly Region[], info: PageInfo): number {
  // The server rounds a strip out to whole pixel rows, so its top is the region's, less under a pixel.
  const onPage = regions.filter((region) => region.page === image.page);
  const distance = (region: Region) => Math.abs((region.y0 ?? 0) - image.y);
  const nearest = onPage.reduce<Region | undefined>(
    (best, region) => (best === undefined || distance(region) < distance(best) ? region : best),
    undefined,
  );
  return nearest?.y1 ?? info.height;
}

/** Whether a strip drawn from `strip.y` to `strip.y1` crosses `row`. */
function crosses(strip: { y: number; y1: number }, row: Row): boolean {
  return row.y0 < strip.y1 && row.y1 > strip.y;
}

/** Whether a strip still shows an edit: a span changed or redacted, or a line put in, within its rows. */
export function stillShows(
  strip: { y: number; y1: number },
  { edits, info }: { edits: PageEdits | undefined; info: PageInfo },
): boolean {
  const spans = edits?.spans.some(({ span }) => crosses(strip, rowOf(span, info))) ?? false;
  const newTexts = edits?.newTexts.some(({ edit }) => crosses(strip, insertRowOf(edit, info))) ?? false;
  return spans || newTexts;
}

/** Whether any of `strips` is drawn over the row of a span's box, or new text's, so the page beneath it doesn't show there. */
export function isUnderStrip(
  box: Row,
  { strips, info }: { strips: readonly { y: number; y1: number }[] | undefined; info: PageInfo },
): boolean {
  const row = rowWithin(info, box.y0, box.y1);
  return strips?.some((strip) => crosses(strip, row)) ?? false;
}

/** The pages whose edits now read other than the reading their strips were drawn from. */
export function stalePages(reading: Reading, drawn: ReadonlyMap<number, Reading>): Set<number> {
  const pages = new Set([...reading.pages.keys(), ...drawn.keys()]);
  return new Set([...pages].filter((page) => !samePage(reading.pages.get(page), drawn.get(page)?.pages.get(page))));
}
