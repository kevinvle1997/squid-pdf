// One request at a time, so an older reply never lands over a newer one.
import { renderStrips } from "../api/client";
import type { Edit, FitInfo, ImageInfo, NoticeInfo, RedactionInfo, Region, Render, SkippedInfo } from "../api/types";
import { sentenceOf } from "../bugs";
import type { Reopener } from "../documents/reopen";
import { RENDER_PAGES_AT_ONCE } from "./constants";
import { differing, type NewTextReading, type PageEdits, type Reading, type SpanReading, sameSpan } from "./project";
import { bottomOf, regionsFor, stalePages } from "./redrawRegions";

export interface Strip {
  readonly page: number;
  readonly y: number;
  readonly y1: number;
  readonly src: string;
}

export type PageFits = Readonly<Record<string, FitInfo>>;

export type PageNewTextFits = Readonly<Record<number, FitInfo>>;

export type PageRedactions = Readonly<Record<string, RedactionInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly Strip[]>;
  readonly from: ReadonlyMap<number, Reading>;
  readonly fits: ReadonlyMap<number, PageFits>;
  readonly newTextFits: ReadonlyMap<number, PageNewTextFits>;
  readonly redactions: ReadonlyMap<number, PageRedactions>;
  readonly notices: ReadonlyMap<number, readonly NoticeInfo[]>;
  readonly skipped: readonly SkippedInfo[];
  readonly failed: string | null;
}

export const NOTHING_DRAWN: Drawn = {
  strips: new Map(),
  from: new Map(),
  fits: new Map(),
  newTextFits: new Map(),
  redactions: new Map(),
  notices: new Map(),
  skipped: [],
  failed: null,
};

export function landedFitOf(drawn: Drawn, now: SpanReading): FitInfo | undefined {
  const { page, id } = now.span;
  const drawnAs = drawn.from.get(page)?.spans.get(id);
  return sameSpan(now, drawnAs) ? drawn.fits.get(page)?.[id] : undefined;
}

export function landedNewTextFitOf(drawn: Drawn, now: NewTextReading): FitInfo | undefined {
  return drawn.newTextFits.get(now.edit.page)?.[now.id];
}

export function landedFits(
  fits: PageFits,
  { now, drawnFrom }: { now: PageEdits | undefined; drawnFrom: PageEdits | undefined },
): PageFits {
  const stale = new Set(differing(now, drawnFrom).map(({ span }) => span.id));
  if (!Object.keys(fits).some((spanId) => stale.has(spanId))) return fits;
  return Object.fromEntries(Object.entries(fits).filter(([spanId]) => !stale.has(spanId)));
}

/** Not read: `build`, since a deploy retires the document. */
type RenderReply = Pick<Render, "fits" | "insert_fits" | "redactions" | "notices" | "skipped">;

function pageOfNotice(reading: Reading, notice: NoticeInfo): number | undefined {
  switch (notice.kind) {
    case "span":
      return reading.spans.get(notice.span_id)?.span.page;
    case "insert": {
      const edit = reading.edits[notice.edit];
      return edit?.kind === "insert" ? edit.page : undefined;
    }
    case "file":
      return undefined;
    default:
      return notice satisfies never;
  }
}

function onPages<V>(reading: Reading, bySpan: Readonly<Record<string, V>>): Map<number, Record<string, V>> {
  const pages = new Map<number, Record<string, V>>();
  for (const [spanId, value] of Object.entries(bySpan)) {
    const page = reading.spans.get(spanId)?.span.page;
    if (page !== undefined) pages.set(page, { ...pages.get(page), [spanId]: value });
  }
  return pages;
}

function newTextFitsOnPages(reading: Reading, reply: RenderReply): Map<number, Record<number, FitInfo>> {
  const sent = new Map<Edit, NewTextReading>();
  for (const edits of reading.pages.values()) for (const insert of edits.newTexts) sent.set(insert.edit, insert);
  const pages = new Map<number, Record<number, FitInfo>>();
  for (const { edit: position, ...fit } of reply.insert_fits) {
    const edit = reading.edits[position];
    const insert = edit === undefined ? undefined : sent.get(edit);
    if (insert !== undefined) pages.set(insert.edit.page, { ...pages.get(insert.edit.page), [insert.id]: fit });
  }
  return pages;
}

function byPage(reading: Reading, reply: RenderReply, drawing: readonly number[]) {
  const fits = onPages(reading, reply.fits);
  const newTextFits = newTextFitsOnPages(reading, reply);
  const redactions = onPages(reading, reply.redactions);
  const notices = new Map<number, NoticeInfo[]>();
  for (const notice of reply.notices) {
    const page = pageOfNotice(reading, notice) ?? drawing[0];
    if (page !== undefined) notices.set(page, [...(notices.get(page) ?? []), notice]);
  }
  return { fits, newTextFits, redactions, notices };
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number;
  drawn: (drawn: Drawn) => void;
  render?: typeof renderStrips;
  load?: (image: ImageInfo) => Promise<string>;
  near?: ReadonlySet<number>;
  extended?: (expiresAt: string) => void;
}

/** Decoded before it goes in, so the swap never flickers. */
async function loaded(image: ImageInfo): Promise<string> {
  const src = `data:image/png;base64,${image.image}`;
  const element = new Image();
  element.src = src;
  // A strip that can't decode still goes in: it shows as missing, not as a stale preview.
  await element.decode().catch(() => undefined);
  return src;
}

export class RenderQueue {
  #drawn = NOTHING_DRAWN;
  #asking: AbortController | null = null;
  #wanted: Reading | null = null;
  readonly #options: Required<Options>;

  constructor(options: Options) {
    this.#options = { render: renderStrips, load: loaded, near: new Set(), extended: () => undefined, ...options };
  }

  get drawn(): Drawn {
    return this.#drawn;
  }

  draw(reading: Reading): void {
    this.#wanted = reading;
    void this.#draw(reading);
  }

  redraw(reading: Reading): void {
    this.#drawn = { ...this.#drawn, from: new Map() };
    this.draw(reading);
  }

  stop(): void {
    this.#asking?.abort();
    this.#asking = null;
    this.#wanted = null;
  }

  retry(): void {
    if (this.#wanted !== null) this.draw(this.#wanted);
  }

  async #draw(reading: Reading): Promise<void> {
    const { reopener, scale, render, load, near } = this.#options;
    this.#asking?.abort();
    this.#asking = null;
    const stale = new Map<number, Region[]>();
    const bare: number[] = [];
    for (const page of stalePages(reading, this.#drawn.from)) {
      if (reading.deleted.has(page)) continue;
      const edits = reading.pages.get(page);
      const info = reopener.doc.pages[page];
      if (edits === undefined || info === undefined) bare.push(page);
      else stale.set(page, regionsFor(page, info, edits));
    }
    if (bare.length > 0) this.#landed(reading, bare, { landing: [], reply: null });
    // The pages near the screen on their own: a render of every page waits on the slowest.
    const shown = [...stale.keys()].filter((page) => near.has(page));
    const drawing = (shown.length > 0 ? shown : [...stale.keys()]).slice(0, RENDER_PAGES_AT_ONCE);
    if (drawing.length === 0) return;
    const regions = drawing.flatMap((page) => stale.get(page) ?? []);

    const ask = new AbortController();
    this.#asking = ask;
    try {
      const reply = await reopener.withDocument((doc) =>
        render(doc.id, { edits: [...reading.edits], scale, regions }, ask.signal),
      );
      const srcs = await Promise.all(reply.images.map(load));
      const strips = reply.images.map(({ page, y }, index) => {
        const info = reopener.doc.pages[page];
        const y1 = info === undefined ? y : bottomOf({ page, y }, regions, info);
        return { page, y, y1, src: srcs[index] ?? "" };
      });
      this.#options.extended(reply.expires_at);
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#landed(reading, drawing, { landing: strips, reply });
      if (drawing.length < stale.size) void this.#draw(reading);
    } catch (error) {
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#drawn = { ...this.#drawn, failed: sentenceOf(error) };
      this.#options.drawn(this.#drawn);
    }
  }

  #landed(
    reading: Reading,
    pages: readonly number[],
    { landing, reply }: { landing: readonly Strip[]; reply: RenderReply | null },
  ) {
    const strips = new Map(this.#drawn.strips);
    const from = new Map(this.#drawn.from);
    const fits = new Map(this.#drawn.fits);
    const newTextFits = new Map(this.#drawn.newTextFits);
    const redactions = new Map(this.#drawn.redactions);
    const notices = new Map(this.#drawn.notices);
    const nothingSaid = { fits: {}, insert_fits: [], redactions: {}, notices: [], skipped: [] };
    const landed = byPage(reading, reply ?? nothingSaid, pages);
    for (const page of pages) {
      const onPage = landing.filter((strip) => strip.page === page);
      if (onPage.length > 0) strips.set(page, onPage);
      else strips.delete(page);
      from.set(page, reading);
      setOrDelete(fits, page, landed.fits.get(page));
      setOrDelete(newTextFits, page, landed.newTextFits.get(page));
      setOrDelete(redactions, page, landed.redactions.get(page));
      setOrDelete(notices, page, landed.notices.get(page));
    }
    const skipped = reply?.skipped ?? (reading.edits.length === 0 ? [] : this.#drawn.skipped);
    const failed = reply === null ? this.#drawn.failed : null;
    this.#drawn = { strips, from, fits, newTextFits, redactions, notices, skipped, failed };
    this.#options.drawn(this.#drawn);
  }
}

function setOrDelete<V>(map: Map<number, V>, page: number, value: V | undefined): void {
  if (value === undefined) map.delete(page);
  else map.set(page, value);
}
