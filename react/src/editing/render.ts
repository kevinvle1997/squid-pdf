// The server's render of each edited row. Typing never waits on it: while a span reads other
// than its page's strips show, the browser previews it. One request at a time, so an older
// reply can never land over a newer one, and a strip goes in only once its images can paint,
// so the swap never flickers. The pages near the screen are asked for first, on their own,
// then the rest. A page whose render failed stays stale: the next change asks for it again,
// and so does `retry`, when the connection comes back.
import { render as renderOnServer } from "../api/client";
import type { FitInfo, ImageInfo, NoticeInfo, RedactionInfo, Region, Render, SkippedInfo } from "../api/types";
import { sentenceOf } from "../bugs";
import type { Reopener } from "../documents/reopen";
import type { Reading } from "./project";
import { bottomOf, regionsFor, stalePages } from "./strips";

/** A strip of a page the server drew, ready to paint: its top and bottom in points, and its image's address. */
export interface Strip {
  readonly page: number;
  readonly y: number;
  readonly y1: number;
  readonly src: string;
}

/** The server's fit for each replaced span on one page, by span id. */
export type PageFits = Readonly<Record<string, FitInfo>>;

/** The server's verdict on each redacted span on one page, by span id. */
export type PageRedactions = Readonly<Record<string, RedactionInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly Strip[]>; // by page
  readonly from: ReadonlyMap<number, Reading>; // by page: the reading its strips were drawn from
  // What a reply says of a page comes and goes with its strips: a page the reply didn't draw keeps its objects, so it isn't drawn again.
  readonly fits: ReadonlyMap<number, PageFits>; // by page: the server's fit for each replaced span on it
  readonly redactions: ReadonlyMap<number, PageRedactions>; // by page: whether each redaction on it holds
  readonly notices: ReadonlyMap<number, readonly NoticeInfo[]>; // by page: what drawing it did other than asked
  readonly skipped: readonly SkippedInfo[]; // the edits the last reply left out, from every page
  readonly failed: string | null; // why the last render failed, in the server's words, until one lands
}

export const NOTHING_DRAWN: Drawn = {
  strips: new Map(),
  from: new Map(),
  fits: new Map(),
  redactions: new Map(),
  notices: new Map(),
  skipped: [],
  failed: null,
};

/**
 * What a reply says, less the images and `expires_at`, which goes to keeping. Not read: `insert_fits`,
 * since nothing inserts yet; and `build`, since a deploy retires the document.
 */
type Said = Pick<Render, "fits" | "redactions" | "notices" | "skipped">;

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

/**
 * A reply's fits, redactions and notices, by the page each is about. The server sends a fit for
 * every replaced span, and a verdict for every redacted one in the regions drawn.
 */
function byPage(reading: Reading, said: Said, drawing: readonly number[]) {
  const fits = onPages(reading, said.fits);
  const redactions = onPages(reading, said.redactions);
  const notices = new Map<number, NoticeInfo[]>();
  for (const notice of said.notices) {
    // One that names no page came from drawing these pages all the same: it goes with the first.
    const page = pageOfNotice(reading, notice) ?? drawing[0];
    if (page !== undefined) notices.set(page, [...(notices.get(page) ?? []), notice]);
  }
  return { fits, redactions, notices };
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number; // pixels per point, as the page images are drawn
  drawn: (drawn: Drawn) => void; // strips went in or came out, or a render failed
  render?: typeof renderOnServer;
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
    this.#options = { render: renderOnServer, load: loaded, near: new Set(), extended: () => undefined, ...options };
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
      const edits = reading.pages.get(page);
      const info = reopener.doc.pages[page];
      // Nothing edited on the page any more: the original image beneath is already right.
      if (edits === undefined || info === undefined) bare.push(page);
      else stale.set(page, regionsFor(page, info, edits));
    }
    if (bare.length > 0) this.#landed(reading, bare, { landing: [], said: null });
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
      this.#landed(reading, drawing, { landing: strips, said: reply });
      // Then the rest, still stale: near the screen first again, as it is now.
      if (drawing.length < stale.size) void this.#draw(reading);
    } catch (error) {
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#drawn = { ...this.#drawn, failed: sentenceOf(error) };
      this.#options.drawn(this.#drawn);
    }
  }

  /** `pages` now show `reading`, in `landing`; `said` is the server's reply, or null for pages it wasn't asked about. */
  #landed(
    reading: Reading,
    pages: readonly number[],
    { landing, said }: { landing: readonly Strip[]; said: Said | null },
  ) {
    const strips = new Map(this.#drawn.strips);
    const from = new Map(this.#drawn.from);
    const fits = new Map(this.#drawn.fits);
    const redactions = new Map(this.#drawn.redactions);
    const notices = new Map(this.#drawn.notices);
    const landed = byPage(reading, said ?? { fits: {}, redactions: {}, notices: [], skipped: [] }, pages);
    for (const page of pages) {
      const onPage = landing.filter((strip) => strip.page === page);
      if (onPage.length > 0) strips.set(page, onPage);
      else strips.delete(page);
      from.set(page, reading);
      setOrDelete(fits, page, landed.fits.get(page));
      setOrDelete(redactions, page, landed.redactions.get(page));
      setOrDelete(notices, page, landed.notices.get(page));
    }
    // Skipped is of the whole edit list: a reply replaces it, and it goes with the last edit.
    const skipped = said?.skipped ?? (reading.edits.length === 0 ? [] : this.#drawn.skipped);
    const failed = said === null ? this.#drawn.failed : null;
    this.#drawn = { strips, from, fits, redactions, notices, skipped, failed };
    this.#options.drawn(this.#drawn);
  }
}

function setOrDelete<V>(map: Map<number, V>, page: number, value: V | undefined): void {
  if (value === undefined) map.delete(page);
  else map.set(page, value);
}
