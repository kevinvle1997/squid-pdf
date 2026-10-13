// Which strips of a page to redraw. Edits stay in their row, so a full-width strip
// around each changed line is byte-identical to the page and a few KB, not a page.
import type { Insert, PageInfo, Region, SpanInfo } from "../api/types";
import { INSERT_ASCENT_EM, INSERT_DESCENT_EM, STRIP_PAD_PT } from "./constants";
import { type PageEdits, type Reading, samePage } from "./project";

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
    ...edits.spans.map((reading) => rowOf(reading.span, page)),
    ...edits.inserts.map((insert) => insertRowOf(insert.edit, page)),
  ];
  return merged(rows).map((row) => ({ page: pageIndex, ...row }));
}

/** Where a strip the server drew ends, in points: the bottom of the region asked for that it tops, the nearest. */
export function bottomOf(image: { page: number; y: number }, regions: readonly Region[], page: PageInfo): number {
  // The server rounds a strip out to whole pixel rows, so its top is the region's, less under a pixel.
  const onPage = regions.filter((region) => region.page === image.page);
  const distance = (region: Region) => Math.abs((region.y0 ?? 0) - image.y);
  const nearest = onPage.reduce<Region | undefined>(
    (best, region) => (best === undefined || distance(region) < distance(best) ? region : best),
    undefined,
  );
  return nearest?.y1 ?? page.height;
}

/** Whether a strip still shows an edit: a span changed or redacted, or a line put in, within its rows. */
export function stillShows(
  strip: { y: number; y1: number },
  { edits, page }: { edits: PageEdits | undefined; page: PageInfo },
): boolean {
  const within = (row: Row) => row.y0 < strip.y1 && row.y1 > strip.y;
  const spans = edits?.spans.some(({ span }) => within(rowOf(span, page))) ?? false;
  const inserts = edits?.inserts.some(({ edit }) => within(insertRowOf(edit, page))) ?? false;
  return spans || inserts;
}

/** The pages whose edits now read other than the reading their strips were drawn from. */
export function stalePages(reading: Reading, drawn: ReadonlyMap<number, Reading>): Set<number> {
  const pages = new Set([...reading.pages.keys(), ...drawn.keys()]);
  return new Set([...pages].filter((page) => !samePage(reading.pages.get(page), drawn.get(page)?.pages.get(page))));
}
