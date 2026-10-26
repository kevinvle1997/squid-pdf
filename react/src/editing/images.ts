// A page image the browser couldn't load: why, on its page in the server's words, and asked for again on a
// backoff and when the connection returns. A document the server lost opens again first: every page's address changes.
import { pageImage } from "../api/client";
import { sentenceOf } from "../bugs";
import { IMAGE_RETRY_FIRST_MS, IMAGE_RETRY_MOST_MS } from "./constants";
import { announcing, type Editor } from "./editor";
import { warn } from "./notices";

/** A page whose image failed, until it loads. */
export interface FailedImage {
  readonly tries: number; // how often it's been asked for again: the image is drawn anew for each
  readonly failed: string | null; // why, in the server's words; null when it answered after all
}

/** A page's image failed to load, for any reason: find out why, say it on the page, and ask again later. */
export async function imageFailed(editor: Editor, page: number): Promise<void> {
  const { reopener } = editor;
  const before = reopener.doc;
  try {
    await reopener.check();
  } catch (error) {
    // Lost, and it couldn't open again: said in the bar, where reopening is.
    const text = sentenceOf(error);
    const { notices } = editor.store.get();
    editor.store.set({ notices: { ...notices, reopen: warn(text) }, announcement: announcing(text) });
    return;
  }
  // Opened again: every page has a new address, so its image comes again by itself.
  if (reopener.doc !== before) return;
  try {
    await pageImage(reopener.doc, page, editor.store.get().scale);
    // It answers now: asked again, after the wait all the same, in case the image itself is what fails.
    failedOnPage(editor, page, null);
  } catch (error) {
    failedOnPage(editor, page, sentenceOf(error));
  }
}

/** A page's image loaded: nothing more to say of it, and nothing to ask again. */
export function imageLoaded(editor: Editor, page: number): void {
  const { images } = editor.store.get();
  if (!images.has(page)) return;
  clearTimeout(editor.retries.get(page));
  editor.retries.delete(page);
  const rest = new Map(images);
  rest.delete(page);
  editor.store.set({ images: rest });
}

/** The connection came back: every page whose image failed is asked for at once. */
export function retryImages(editor: Editor): void {
  for (const page of editor.store.get().images.keys()) askAgain(editor, page);
}

/** Say why on the page, heard unless another page says it already, and ask again after a wait that grows. */
function failedOnPage(editor: Editor, page: number, why: string | null): void {
  const { images } = editor.store.get();
  const image = images.get(page) ?? { tries: 0, failed: null };
  const elsewhere = [...images].some(([other, { failed }]) => other !== page && failed === why);
  const heard = why !== null && why !== image.failed && !elsewhere;
  editor.store.set({
    images: new Map(images).set(page, { ...image, failed: why ?? image.failed }),
    ...(heard && { announcement: announcing(why) }),
  });
  clearTimeout(editor.retries.get(page));
  const wait = Math.min(IMAGE_RETRY_FIRST_MS * 2 ** image.tries, IMAGE_RETRY_MOST_MS);
  editor.retries.set(
    page,
    setTimeout(() => askAgain(editor, page), wait),
  );
}

/** Draw the page's image anew, so the browser asks for it again. */
function askAgain(editor: Editor, page: number): void {
  clearTimeout(editor.retries.get(page));
  editor.retries.delete(page);
  const { images } = editor.store.get();
  const image = images.get(page);
  if (image === undefined) return;
  editor.store.set({ images: new Map(images).set(page, { ...image, tries: image.tries + 1 }) });
}
