// The server's render of each edited row. Typing never waits on it: while a span reads other
// than its page's strips show, the browser previews it. One request at a time, so an older
// reply can never land over a newer one, and a strip goes in only once its images can paint,
// so the swap never flickers. A page whose render failed stays stale: the next change asks
// for it again, and so does `retry`, when the connection comes back.
import { ProblemError, render as renderOnServer } from "../api/client";
import type { FitInfo, ImageInfo, Region, Render } from "../api/types";
import type { Reopener } from "../documents/reopen";
import type { EditedView } from "./project";
import { regionsFor, stalePages } from "./strips";

/** The server's fit for each replaced span on one page, by span id. */
export type PageFits = Readonly<Record<string, FitInfo>>;

export interface Drawn {
  readonly strips: ReadonlyMap<number, readonly ImageInfo[]>; // by page
  readonly from: ReadonlyMap<number, EditedView>; // by page: the reading its strips were drawn from
  // By page, and replaced only as that page's strips are: a page whose fits didn't come keeps its object, so it isn't drawn again.
  readonly fits: ReadonlyMap<number, PageFits>;
}

export const NOTHING_DRAWN: Drawn = { strips: new Map(), from: new Map(), fits: new Map() };

/** The reply's fits, by the page each span is on. The server sends one for every replaced span. */
function fitsByPage(view: EditedView, fits: Render["fits"]): Map<number, Record<string, FitInfo>> {
  const byPage = new Map<number, Record<string, FitInfo>>();
  for (const [spanId, fit] of Object.entries(fits)) {
    const page = view.spans.get(spanId)?.span.page;
    if (page === undefined) continue;
    byPage.set(page, { ...byPage.get(page), [spanId]: fit });
  }
  return byPage;
}

interface Options {
  reopener: Pick<Reopener, "doc" | "withDocument">;
  scale: number; // pixels per point, as the page images are drawn
  drawn: (drawn: Drawn) => void; // strips went in or came out
  failed: (detail: string) => void; // a render failed, in the server's words; its pages stay stale
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
    const { reopener, scale, render, decode, failed } = this.#options;
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
    if (bare.length > 0) this.#landed(view, bare, [], {});
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
      this.#landed(view, drawing, reply.images, reply.fits);
    } catch (error) {
      if (ask.signal.aborted) return;
      if (!(error instanceof ProblemError)) throw error;
      this.#asking = null;
      failed(error.problem.detail);
    }
  }

  /** `pages` now show `view`, in `images`. */
  #landed(view: EditedView, pages: readonly number[], images: readonly ImageInfo[], replyFits: Render["fits"]) {
    const strips = new Map(this.#drawn.strips);
    const from = new Map(this.#drawn.from);
    const fits = new Map(this.#drawn.fits);
    const landedFits = fitsByPage(view, replyFits);
    for (const page of pages) {
      const onPage = images.filter((image) => image.page === page);
      if (onPage.length > 0) strips.set(page, onPage);
      else strips.delete(page);
      from.set(page, view);
      const pageFits = landedFits.get(page);
      if (pageFits !== undefined) fits.set(page, pageFits);
      else fits.delete(page);
    }
    this.#drawn = { strips, from, fits };
    this.#options.drawn(this.#drawn);
  }
}
