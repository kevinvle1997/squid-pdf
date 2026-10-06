// The server's render of each edited row. Typing never waits on it: while a span reads other
// than its page's strips show, the browser previews it. One request at a time, so an older
// reply can never land over a newer one, and a strip goes in only once its images can paint,
// so the swap never flickers. A page whose render failed stays stale: the next change asks
// for it again, and so does `retry`, when the connection comes back.
import { ProblemError, render as renderOnServer } from "../api/client";
import type { FitInfo, ImageInfo, NoticeInfo, Region, Render, SkippedInfo } from "../api/types";
import { reportBug } from "../bugs";
import type { Reopener } from "../documents/reopen";
import type { Reading } from "./project";
import { regionsFor, stalePages } from "./strips";

/** A strip of a page the server drew, ready to paint: its top in points, and its image's address. */
export interface Strip {
  readonly page: number;
  readonly y: number;
  readonly src: string;
}

/** The server's fit for each replaced span on one page, by span id. */
export type PageFits = Readonly<Record<string, FitInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly Strip[]>; // by page
  readonly from: ReadonlyMap<number, Reading>; // by page: the reading its strips were drawn from
  // What a reply says of a page comes and goes with its strips: a page the reply didn't draw keeps its objects, so it isn't drawn again.
  readonly fits: ReadonlyMap<number, PageFits>; // by page: the server's fit for each replaced span on it
  readonly notices: ReadonlyMap<number, readonly NoticeInfo[]>; // by page: what drawing it did other than asked
  readonly skipped: readonly SkippedInfo[]; // the edits the last reply left out, from every page
  readonly failed: string | null; // why the last render failed, in the server's words, until one lands
}

export const NOTHING_DRAWN: Drawn = {
  strips: new Map(),
  from: new Map(),
  fits: new Map(),
  notices: new Map(),
  skipped: [],
  failed: null,
};

/**
 * What a reply says, less the images. Not read yet: `insert_fits` and `redactions`, since
 * nothing inserts or redacts yet, and `build` and `expires_at`, which the document has.
 */
type Said = Pick<Render, "fits" | "notices" | "skipped">;

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

/** A reply's fits and notices, by the page each is about. The server sends a fit for every replaced span. */
function byPage(reading: Reading, said: Said, drawing: readonly number[]) {
  const fits = new Map<number, Record<string, FitInfo>>();
  for (const [spanId, fit] of Object.entries(said.fits)) {
    const page = reading.spans.get(spanId)?.span.page;
    if (page !== undefined) fits.set(page, { ...fits.get(page), [spanId]: fit });
  }
  const notices = new Map<number, NoticeInfo[]>();
  for (const notice of said.notices) {
    // One that names no page came from drawing these pages all the same: it goes with the first.
    const page = pageOfNotice(reading, notice) ?? drawing[0];
    if (page !== undefined) notices.set(page, [...(notices.get(page) ?? []), notice]);
  }
  return { fits, notices };
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number; // pixels per point, as the page images are drawn
  drawn: (drawn: Drawn) => void; // strips went in or came out, or a render failed
  render?: typeof renderOnServer;
  load?: (image: ImageInfo) => Promise<string>; // its address, once it can paint
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
    this.#options = { render: renderOnServer, load: loaded, ...options };
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
    const { reopener, scale, render, load } = this.#options;
    // A newer reading takes over what an older request was still drawing: those pages are still stale.
    this.#asking?.abort();
    this.#asking = null;
    const drawing: number[] = [];
    const bare: number[] = [];
    const regions: Region[] = [];
    for (const page of stalePages(reading, this.#drawn.from)) {
      const edits = reading.pages.get(page);
      const info = reopener.doc.pages[page];
      // Nothing edited on the page any more: the original image beneath is already right.
      if (edits === undefined || info === undefined) {
        bare.push(page);
        continue;
      }
      drawing.push(page);
      regions.push(...regionsFor(page, info, edits));
    }
    if (bare.length > 0) this.#landed(reading, bare, { landing: [], said: null });
    if (drawing.length === 0) return;

    const ask = new AbortController();
    this.#asking = ask;
    try {
      // Still ours while the document opens again, so a newer change can take these pages over.
      const reply = await reopener.withDocument((doc) =>
        render(doc.id, { edits: [...reading.edits], scale, regions }, ask.signal),
      );
      const srcs = await Promise.all(reply.images.map(load));
      const strips = reply.images.map(({ page, y }, index) => ({ page, y, src: srcs[index] ?? "" }));
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#landed(reading, drawing, { landing: strips, said: reply });
    } catch (error) {
      if (ask.signal.aborted) return;
      this.#asking = null;
      const failed = error instanceof ProblemError ? error.problem.detail : reportBug(error);
      this.#drawn = { ...this.#drawn, failed };
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
    const notices = new Map(this.#drawn.notices);
    const landed = byPage(reading, said ?? { fits: {}, notices: [], skipped: [] }, pages);
    for (const page of pages) {
      const onPage = landing.filter((strip) => strip.page === page);
      if (onPage.length > 0) strips.set(page, onPage);
      else strips.delete(page);
      from.set(page, reading);
      setOrDelete(fits, page, landed.fits.get(page));
      setOrDelete(notices, page, landed.notices.get(page));
    }
    // Skipped is of the whole edit list: a reply replaces it, and it goes with the last edit.
    const skipped = said?.skipped ?? (reading.edits.length === 0 ? [] : this.#drawn.skipped);
    const failed = said === null ? this.#drawn.failed : null;
    this.#drawn = { strips, from, fits, notices, skipped, failed };
    this.#options.drawn(this.#drawn);
  }
}

function setOrDelete<V>(map: Map<number, V>, page: number, value: V | undefined): void {
  if (value === undefined) map.delete(page);
  else map.set(page, value);
}
