// The server's render of each edited row, one request at a time so an older reply never lands over a
// newer one. Typing never waits on it: the browser previews a span until a strip shows it.
import { renderStrips } from "../api/client";
import type { Edit, FitInfo, ImageInfo, NoticeInfo, RedactionInfo, Region, Render, SkippedInfo } from "../api/types";
import { sentenceOf } from "../bugs";
import type { Reopener } from "../documents/reopen";
import { differing, type InsertReading, type PageEdits, type Reading, type SpanReading, sameSpan } from "./project";
import { bottomOf, regionsFor, stalePages } from "./redrawRegions";

/** A strip of a page the server drew, ready to paint: its top and bottom in points, and its image's address. */
export interface Strip {
  readonly page: number;
  readonly y: number;
  readonly y1: number;
  readonly src: string;
}

/** The server's fit for each replaced span on one page, by span id. */
export type PageFits = Readonly<Record<string, FitInfo>>;

/** The server's fit for each piece of new text on one page, by the id of the version it was drawn from. */
export type PageInsertFits = Readonly<Record<number, FitInfo>>;

/** The server's verdict on each redacted span on one page, by span id. */
export type PageRedactions = Readonly<Record<string, RedactionInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly Strip[]>; // by page
  readonly from: ReadonlyMap<number, Reading>; // by page: the reading its strips were drawn from
  // What a reply says of a page comes and goes with its strips: a page the reply didn't draw keeps its objects, so it isn't drawn again.
  readonly fits: ReadonlyMap<number, PageFits>; // by page: the server's fit for each replaced span on it
  readonly insertFits: ReadonlyMap<number, PageInsertFits>; // by page: the server's fit for each piece of new text on it
  readonly redactions: ReadonlyMap<number, PageRedactions>; // by page: whether each redaction on it holds
  readonly notices: ReadonlyMap<number, readonly NoticeInfo[]>; // by page: what drawing it did other than asked
  readonly skipped: readonly SkippedInfo[]; // the edits the last reply left out, from every page
  readonly failed: string | null; // why the last render failed, in the server's words, until one lands
}

export const NOTHING_DRAWN: Drawn = {
  strips: new Map(),
  from: new Map(),
  fits: new Map(),
  insertFits: new Map(),
  redactions: new Map(),
  notices: new Map(),
  skipped: [],
  failed: null,
};

/** The server's fit for a span as `now` reads it, once a render of it reading so has landed; none before. */
export function landedFitOf(drawn: Drawn, now: SpanReading): FitInfo | undefined {
  const { page, id } = now.span;
  const drawnAs = drawn.from.get(page)?.spans.get(id);
  return sameSpan(now, drawnAs) ? drawn.fits.get(page)?.[id] : undefined;
}

/** The server's fit for new text as `now` has it, once a render of that very version landed; none before. */
export function landedInsertFitOf(drawn: Drawn, now: InsertReading): FitInfo | undefined {
  return drawn.insertFits.get(now.edit.page)?.[now.id];
}

/** A page's fits, less those of spans its strips were drawn from other words for: those say nothing of the words now. */
export function landedFits(
  fits: PageFits,
  { now, drawnFrom }: { now: PageEdits | undefined; drawnFrom: PageEdits | undefined },
): PageFits {
  const stale = new Set(differing(now, drawnFrom).map(({ span }) => span.id));
  if (!Object.keys(fits).some((spanId) => stale.has(spanId))) return fits;
  return Object.fromEntries(Object.entries(fits).filter(([spanId]) => !stale.has(spanId)));
}

/** What a reply says, less the images and `expires_at`, which goes to `persistUntil`. Not read: `build`, since a deploy retires the document. */
type RenderReply = Pick<Render, "fits" | "insert_fits" | "redactions" | "notices" | "skipped">;

/** The page a notice is about: its span's, or its insert's. A notice about the whole file names none. */
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

/** Each span's entry in `bySpan`, by the page its span is on. */
function onPages<V>(reading: Reading, bySpan: Readonly<Record<string, V>>): Map<number, Record<string, V>> {
  const pages = new Map<number, Record<string, V>>();
  for (const [spanId, value] of Object.entries(bySpan)) {
    const page = reading.spans.get(spanId)?.span.page;
    if (page !== undefined) pages.set(page, { ...pages.get(page), [spanId]: value });
  }
  return pages;
}

/** Each fit of new text in `reply`, by the page it's on, under the version it was drawn from: named by its place in the edits sent. */
function insertFitsOnPages(reading: Reading, reply: RenderReply): Map<number, Record<number, FitInfo>> {
  const sent = new Map<Edit, InsertReading>();
  for (const edits of reading.pages.values()) for (const insert of edits.inserts) sent.set(insert.edit, insert);
  const pages = new Map<number, Record<number, FitInfo>>();
  for (const { edit: position, ...fit } of reply.insert_fits) {
    const edit = reading.edits[position];
    const insert = edit === undefined ? undefined : sent.get(edit);
    if (insert !== undefined) pages.set(insert.edit.page, { ...pages.get(insert.edit.page), [insert.id]: fit });
  }
  return pages;
}

/**
 * A reply's fits, redactions and notices, by the page each is about. The server sends a fit for
 * every replaced span and piece of new text, and a verdict for every redacted span in the regions drawn.
 */
function byPage(reading: Reading, reply: RenderReply, drawing: readonly number[]) {
  const fits = onPages(reading, reply.fits);
  const insertFits = insertFitsOnPages(reading, reply);
  const redactions = onPages(reading, reply.redactions);
  const notices = new Map<number, NoticeInfo[]>();
  for (const notice of reply.notices) {
    // One that names no page came from drawing these pages all the same: it goes with the first.
    const page = pageOfNotice(reading, notice) ?? drawing[0];
    if (page !== undefined) notices.set(page, [...(notices.get(page) ?? []), notice]);
  }
  return { fits, insertFits, redactions, notices };
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number; // pixels per point, as the page images are drawn
  drawn: (drawn: Drawn) => void; // strips went in or came out, or a render failed
  render?: typeof renderStrips;
  load?: (image: ImageInfo) => Promise<string>; // its address, once it can paint
  near?: ReadonlySet<number>; // the pages near the screen, as they come and go: asked for first
  extended?: (expiresAt: string) => void; // a reply came, and with it when the server's hour now ends
}

/**
 * The PNG the server sent, as the address the page's `<img>` shows it at: written once, not on
 * every draw of its page, and decoded before it goes in, so the swap never flickers.
 */
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
  #wanted: Reading | null = null; // the last reading asked for
  readonly #options: Required<Options>;

  constructor(options: Options) {
    this.#options = { render: renderStrips, load: loaded, near: new Set(), extended: () => undefined, ...options };
  }

  get drawn(): Drawn {
    return this.#drawn;
  }

  /** Bring every page's strips up to `reading`. */
  draw(reading: Reading): void {
    this.#wanted = reading;
    void this.#draw(reading);
  }

  /** Draw every edited page again, though its edits didn't change: what draws them did. */
  redraw(reading: Reading): void {
    this.#drawn = { ...this.#drawn, from: new Map() };
    this.draw(reading);
  }

  /** Stop: what's in flight is dropped, and nothing is asked for again until the next draw. */
  stop(): void {
    this.#asking?.abort();
    this.#asking = null;
    this.#wanted = null;
  }

  /** Ask again for whatever is still stale. */
  retry(): void {
    if (this.#wanted !== null) this.draw(this.#wanted);
  }

  async #draw(reading: Reading): Promise<void> {
    const { reopener, scale, render, load, near } = this.#options;
    // A newer reading takes over what an older request was still drawing: those pages are still stale.
    this.#asking?.abort();
    this.#asking = null;
    const stale = new Map<number, Region[]>();
    const bare: number[] = [];
    for (const page of stalePages(reading, this.#drawn.from)) {
      // Left out of the download, it shows no sheet: still stale, it's drawn once it's put back.
      if (reading.deleted.has(page)) continue;
      const edits = reading.pages.get(page);
      const info = reopener.doc.pages[page];
      // Nothing edited on the page any more: the original image beneath is already right.
      if (edits === undefined || info === undefined) bare.push(page);
      else stale.set(page, regionsFor(page, info, edits));
    }
    if (bare.length > 0) this.#landed(reading, bare, { landing: [], reply: null });
    // The pages near the screen on their own: a render of every page waits on the slowest.
    const shown = [...stale.keys()].filter((page) => near.has(page));
    const drawing = shown.length > 0 ? shown : [...stale.keys()];
    if (drawing.length === 0) return;
    const regions = drawing.flatMap((page) => stale.get(page) ?? []);

    const ask = new AbortController();
    this.#asking = ask;
    try {
      // Still ours while the document opens again, so a newer change can take these pages over.
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
      // Then the rest, still stale: near the screen first again, as it is now.
      if (drawing.length < stale.size) void this.#draw(reading);
    } catch (error) {
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#drawn = { ...this.#drawn, failed: sentenceOf(error) };
      this.#options.drawn(this.#drawn);
    }
  }

  /** `pages` now show `reading`, in `landing`; `reply` is the server's, or null for pages it wasn't asked about. */
  #landed(
    reading: Reading,
    pages: readonly number[],
    { landing, reply }: { landing: readonly Strip[]; reply: RenderReply | null },
  ) {
    const strips = new Map(this.#drawn.strips);
    const from = new Map(this.#drawn.from);
    const fits = new Map(this.#drawn.fits);
    const insertFits = new Map(this.#drawn.insertFits);
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
      setOrDelete(insertFits, page, landed.insertFits.get(page));
      setOrDelete(redactions, page, landed.redactions.get(page));
      setOrDelete(notices, page, landed.notices.get(page));
    }
    // Skipped is of the whole edit list: a reply replaces it, and it goes with the last edit.
    const skipped = reply?.skipped ?? (reading.edits.length === 0 ? [] : this.#drawn.skipped);
    const failed = reply === null ? this.#drawn.failed : null;
    this.#drawn = { strips, from, fits, insertFits, redactions, notices, skipped, failed };
    this.#options.drawn(this.#drawn);
  }
}

function setOrDelete<V>(map: Map<number, V>, page: number, value: V | undefined): void {
  if (value === undefined) map.delete(page);
  else map.set(page, value);
}
