// The server's render of each edited row. Typing never waits on it: while a span reads other
// than its page's strips show, the browser previews it. One request at a time, so an older
// reply can never land over a newer one, and a strip goes in only once its images can paint,
// so the swap never flickers. A page whose render failed stays stale: the next change asks
// for it again, and so does `retry`, when the connection comes back.
import { ProblemError, render as renderOnServer } from "../api/client";
import type { FitInfo, ImageInfo, NoticeInfo, Region, Render, SkippedInfo } from "../api/types";
import { reportBug } from "../bugs";
import type { Reopener } from "../documents/reopen";
import type { EditedView } from "./project";
import { regionsFor, stalePages } from "./strips";

/** The server's fit for each replaced span on one page, by span id. */
export type PageFits = Readonly<Record<string, FitInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly ImageInfo[]>; // by page
  readonly from: ReadonlyMap<number, EditedView>; // by page: the reading its strips were drawn from
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

/** What a reply says, less the images. */
type Said = Pick<Render, "fits" | "notices" | "skipped">;

/** The page a notice is about: its span's, or its edit's. */
function pageOfNotice(view: EditedView, notice: NoticeInfo): number | undefined {
  const edit = notice.edit === null ? undefined : view.edits[notice.edit];
  if (edit?.kind === "insert") return edit.page;
  const spanId = notice.span_id ?? edit?.span_id;
  return spanId === undefined ? undefined : view.spans.get(spanId)?.span.page;
}

/** A reply's fits and notices, by the page each is about. The server sends a fit for every replaced span. */
function byPage(view: EditedView, said: Said, drawing: readonly number[]) {
  const fits = new Map<number, Record<string, FitInfo>>();
  for (const [spanId, fit] of Object.entries(said.fits)) {
    const page = view.spans.get(spanId)?.span.page;
    if (page !== undefined) fits.set(page, { ...fits.get(page), [spanId]: fit });
  }
  const notices = new Map<number, NoticeInfo[]>();
  for (const notice of said.notices) {
    // One that names neither span nor edit came from drawing these pages all the same: it goes with the first.
    const page = pageOfNotice(view, notice) ?? drawing[0];
    if (page !== undefined) notices.set(page, [...(notices.get(page) ?? []), notice]);
  }
  return { fits, notices };
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number; // pixels per point, as the page images are drawn
  drawn: (drawn: Drawn) => void; // strips went in or came out, or a render failed
  render?: typeof renderOnServer;
  decode?: (image: ImageInfo) => Promise<void>;
}

function decoded(image: ImageInfo): Promise<void> {
  const element = new Image();
  element.src = `data:image/png;base64,${image.image}`;
  // A strip that can't decode still goes in: it shows as missing, not as a stale preview.
  return element.decode().catch(() => undefined);
}

export class RenderQueue {
  #drawn = NOTHING_DRAWN;
  #asking: AbortController | null = null;
  #wanted: EditedView | null = null; // the last reading asked for
  readonly #options: Required<Options>;

  constructor(options: Options) {
    this.#options = { render: renderOnServer, decode: decoded, ...options };
  }

  get drawn(): Drawn {
    return this.#drawn;
  }

  /** Bring every page's strips up to `view`. */
  draw(view: EditedView): void {
    this.#wanted = view;
    void this.#draw(view);
  }

  /** Ask again for whatever is still stale. */
  retry(): void {
    if (this.#wanted !== null) this.draw(this.#wanted);
  }

  async #draw(view: EditedView): Promise<void> {
    const { reopener, scale, render, decode } = this.#options;
    // A newer reading takes over what an older request was still drawing: those pages are still stale.
    this.#asking?.abort();
    this.#asking = null;
    const drawing: number[] = [];
    const bare: number[] = [];
    const regions: Region[] = [];
    for (const page of stalePages(view, this.#drawn.from)) {
      const edits = view.pages.get(page);
      const info = reopener.doc.pages[page];
      // Nothing edited on the page any more: the original image beneath is already right.
      if (edits === undefined || info === undefined) {
        bare.push(page);
        continue;
      }
      drawing.push(page);
      regions.push(...regionsFor(page, info, edits));
    }
    if (bare.length > 0) this.#landed(view, bare, [], null);
    if (drawing.length === 0) return;

    const ask = new AbortController();
    this.#asking = ask;
    try {
      // Still ours while the document opens again, so a newer change can take these pages over.
      const reply = await reopener.withDocument((doc) =>
        render(doc.id, { edits: [...view.edits], scale, regions }, ask.signal),
      );
      await Promise.all(reply.images.map(decode));
      if (ask.signal.aborted) return;
      this.#asking = null;
      this.#landed(view, drawing, reply.images, reply);
    } catch (error) {
      if (ask.signal.aborted) return;
      this.#asking = null;
      const failed = error instanceof ProblemError ? error.problem.detail : reportBug(error);
      this.#drawn = { ...this.#drawn, failed };
      this.#options.drawn(this.#drawn);
    }
  }

  /** `pages` now show `view`, in `images`; `said` is the server's reply, or null for pages it wasn't asked about. */
  #landed(view: EditedView, pages: readonly number[], images: readonly ImageInfo[], said: Said | null) {
    const strips = new Map(this.#drawn.strips);
    const from = new Map(this.#drawn.from);
    const fits = new Map(this.#drawn.fits);
    const notices = new Map(this.#drawn.notices);
    const landed = byPage(view, said ?? { fits: {}, notices: [], skipped: [] }, pages);
    for (const page of pages) {
      const onPage = images.filter((image) => image.page === page);
      if (onPage.length > 0) strips.set(page, onPage);
      else strips.delete(page);
      from.set(page, view);
      setOrDelete(fits, page, landed.fits.get(page));
      setOrDelete(notices, page, landed.notices.get(page));
    }
    // Skipped is of the whole edit list: a reply replaces it, and it goes with the last edit.
    const skipped = said?.skipped ?? (view.edits.length === 0 ? [] : this.#drawn.skipped);
    const failed = said === null ? this.#drawn.failed : null;
    this.#drawn = { strips, from, fits, notices, skipped, failed };
    this.#options.drawn(this.#drawn);
  }
}

function setOrDelete<V>(map: Map<number, V>, page: number, value: V | undefined): void {
  if (value === undefined) map.delete(page);
  else map.set(page, value);
}
