// Page images, loaded outside the page a few at a time, the page being read's first: the browser would load every near
// page at once, and go on loading those scrolled past. A page draws the very element loaded, so it fetches nothing again.
import { type ImageLoading, loadImage, pageUrl } from "../api/client";
import { IMAGE_INTENT_MS, IMAGES_AT_ONCE } from "./constants";
import type { Editor } from "./editor";
import { imageFailed, imageLoaded } from "./images";
import { loadShippedFaces } from "./shippedFaces";

/** A page's image, by its address now: the document's, at the scale the pages are drawn at. */
export interface PageImage {
  readonly page: number;
  readonly url: string;
  readonly inView?: boolean; // the page being read's: loaded ahead of the rest
}

/** A page's image in, or failed: what the queue says of each. */
type Done = (image: PageImage, { loaded }: { loaded: boolean }) => void;

/**
 * Page images loading, the most wanted first and a few at once: one no longer wanted stops. One that's in is kept for
 * its page to draw, until one at its new address is in; one that failed waits to be asked for again.
 */
export class PageImageQueue {
  readonly #loading = new Map<string, ImageLoading>(); // by address
  readonly #images = new Map<string, HTMLImageElement>(); // by address: in, for the page to draw whenever it's near
  readonly #addressOf = new Map<number, string>(); // by page: the address of the image kept for it
  readonly #failed = new Set<string>(); // by address: until it's asked for again
  #wanted: readonly PageImage[] = [];
  readonly #done: Done;
  readonly #load: typeof loadImage;

  constructor({ done, load = loadImage }: { done: Done; load?: typeof loadImage }) {
    this.#done = done;
    this.#load = load;
  }

  /** The element holding the image at `url`, once it's in. */
  imageAt(url: string): HTMLImageElement | undefined {
    return this.#images.get(url);
  }

  /** Load `wanted`, the first first: what's loading and isn't among them stops, and what's in already draws. */
  load(wanted: readonly PageImage[]): void {
    this.#wanted = wanted;
    const urls = new Set(wanted.map(({ url }) => url));
    for (const [url, loading] of this.#loading) {
      if (urls.has(url)) continue;
      loading.stop();
      this.#loading.delete(url);
    }
    for (const image of wanted) if (this.#images.has(image.url)) this.#done(image, { loaded: true });
    this.#next();
  }

  /** The image at `url` failed and its wait is over: it's loaded again, when it's wanted. */
  retry(url: string): void {
    if (this.#failed.delete(url)) this.#next();
  }

  /** Stop all of it: the editor is closing. */
  stop(): void {
    this.load([]);
  }

  #next(): void {
    for (const image of this.#wanted) {
      if (this.#loading.size >= IMAGES_AT_ONCE) return;
      const waiting = this.#images.has(image.url) || this.#loading.has(image.url) || this.#failed.has(image.url);
      if (!waiting) this.#start(image);
    }
  }

  #start(image: PageImage): void {
    const loading = this.#load(image.url, { inView: image.inView ?? false });
    this.#loading.set(image.url, loading);
    void loading.loaded.then((loaded) => {
      // Stopped meanwhile: scrolled past, or its address changed.
      if (this.#loading.get(image.url) !== loading) return;
      this.#loading.delete(image.url);
      if (loaded) this.#keep(image, loading.image);
      else this.#failed.add(image.url);
      this.#done(image, { loaded });
      this.#next();
    });
  }

  /** Keep `element`, page `image.page`'s image at its address now, in place of the one before: a page never goes back to that one. */
  #keep({ page, url }: PageImage, element: HTMLImageElement): void {
    const before = this.#addressOf.get(page);
    if (before !== undefined) this.#images.delete(before);
    this.#addressOf.set(page, url);
    this.#images.set(url, element);
  }
}

/** Load the images of the pages near the screen, nearest the page being read first, then those the reader is on the way to. */
export function loadPageImages(editor: Editor): void {
  const { doc, scale, pageInView } = editor.store.get();
  // Pages are drawn in the original's order, a deleted one in its place.
  const near = [...editor.near].sort((a, b) => Math.abs(a - pageInView) - Math.abs(b - pageInView));
  const pages = new Set([...near, ...editor.imagesAhead.pages]);
  const wanted = [...pages].map((page) => ({ page, url: pageUrl(doc, page, scale), inView: page === pageInView }));
  editor.imageQueue.load(wanted);
}

/** The reader is on the way to `pages`: their images load next, after the screen's, in place of those before. */
export function loadAhead(editor: Editor, pages: readonly number[]): void {
  clearTimeout(editor.imagesAhead.timer);
  editor.imagesAhead.pages = pages;
  loadPageImages(editor);
}

/**
 * The pointer rests on the way to `pages`, or focus stays there: their images load ahead once it's rested a moment,
 * so a sweep across them loads none.
 */
export function loadAheadOnRest(editor: Editor, pages: readonly number[]): void {
  clearTimeout(editor.imagesAhead.timer);
  editor.imagesAhead.timer = setTimeout(() => loadAhead(editor, pages), IMAGE_INTENT_MS);
}

/**
 * The reader is no longer on the way anywhere: nothing more loads ahead. A load under way goes on, since a press
 * scrolls its page near as focus leaves the tick; the screen's next ask leaves it out if it isn't.
 */
export function nothingAhead(editor: Editor): void {
  clearTimeout(editor.imagesAhead.timer);
  editor.imagesAhead.pages = [];
}

/**
 * A page's image is in, or failed: the page draws it, or says why and asks again later. The font list waits for the
 * first, so it shares neither the link nor the main thread with it.
 */
export function imageDone(editor: Editor, { page, url }: PageImage, { loaded }: { loaded: boolean }): void {
  void loadShippedFaces(editor);
  if (!loaded) {
    void imageFailed(editor, page);
    return;
  }
  const { loadedImages } = editor.store.get();
  if (loadedImages.get(page) !== url) editor.store.set({ loadedImages: new Map(loadedImages).set(page, url) });
  imageLoaded(editor, page);
}

/** Load page `page`'s image again, its wait after a failure over: it's drawn once it's in. */
export function retryPageImage(editor: Editor, page: number): void {
  const { doc, scale } = editor.store.get();
  editor.imageQueue.retry(pageUrl(doc, page, scale));
}
