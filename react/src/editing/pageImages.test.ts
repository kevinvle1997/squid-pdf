import { afterEach, expect, test, vi } from "vitest";
import { loadImage, pageUrl } from "../api/client";
import type { Render } from "../api/types";
import { A4, aDeletePage, aDoc } from "../fixtures";
import { IMAGE_INTENT_MS } from "./constants";
import { change, closeEditor, createEditor, type Editor } from "./editor";
import { closeFind, openFind } from "./find";
import { loadAhead, loadAheadOnRest, loadPageImages, nothingAhead, type PageImage, PageImageQueue } from "./pageImages";
import { zoomBy } from "./zoom";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  loadImage: vi.fn(),
  renderStrips: vi.fn(() => new Promise<Render>(() => undefined)),
}));

/** Images that come in, or fail, when a test says, each noting whether it was stopped first. */
function images() {
  const started: { url: string; come: (loaded: boolean) => void; stopped: boolean }[] = [];
  const load = (url: string) => {
    let come: (loaded: boolean) => void = () => undefined;
    const loaded = new Promise<boolean>((resolve) => {
      come = resolve;
    });
    const loading = { url, come, stopped: false };
    started.push(loading);
    return {
      image: { src: url } as HTMLImageElement,
      loaded,
      stop: () => {
        loading.stopped = true;
      },
    };
  };
  return { started, load, urls: () => started.map(({ url }) => url) };
}

/** Page `page`'s image, drawn at `scale`. */
const pageImage = (page: number, scale = 2): PageImage => ({ page, url: `/pages/${page}?scale=${scale}` });

/** Let the queue's callbacks for the images that came in run. */
const callbacksRun = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A queue loading with `load`, and what it said of each image it was done with. */
function queueOf(load: ReturnType<typeof images>["load"]) {
  const done: [PageImage, boolean][] = [];
  const queue = new PageImageQueue({ load, done: (image, { loaded }) => done.push([image, loaded]) });
  return { queue, done };
}

let editor: Editor | null = null;

afterEach(() => {
  if (editor !== null) closeEditor(editor);
  editor = null;
  vi.useRealTimers();
});

test("the most wanted loads first, two at once, and the next as one comes in", async () => {
  const { started, load, urls } = images();
  const { queue, done } = queueOf(load);
  queue.load([pageImage(3), pageImage(2), pageImage(4)]);
  expect(urls()).toEqual([pageImage(3).url, pageImage(2).url]);
  started[1]?.come(true);
  await callbacksRun();
  expect(done).toEqual([[pageImage(2), true]]);
  expect(urls()).toEqual([pageImage(3).url, pageImage(2).url, pageImage(4).url]);
});

test("a page no longer wanted, scrolled past, stops loading and is never drawn", async () => {
  const { started, load, urls } = images();
  const { queue, done } = queueOf(load);
  queue.load([pageImage(0), pageImage(1)]);
  queue.load([pageImage(1), pageImage(2)]);
  expect(started[0]?.stopped).toBe(true);
  expect(urls()).toEqual([pageImage(0).url, pageImage(1).url, pageImage(2).url]);
  started[0]?.come(true);
  await callbacksRun();
  expect(done).toEqual([]);
});

test("a page's image at a new address loads again, and one in already is drawn again at once, the very element", async () => {
  const { started, load, urls } = images();
  const { queue, done } = queueOf(load);
  queue.load([pageImage(0)]);
  started[0]?.come(true);
  await callbacksRun();
  // Zoomed in: drawn sharper, at a new address.
  queue.load([pageImage(0, 3)]);
  expect(urls()).toEqual([pageImage(0).url, pageImage(0, 3).url]);
  queue.load([pageImage(0)]);
  expect(done).toEqual([
    [pageImage(0), true],
    [pageImage(0), true],
  ]);
  expect(queue.imageAt(pageImage(0).url)?.src).toBe(pageImage(0).url);
});

test("a page's image at its new address, once in, is kept in place of the one before", async () => {
  const { started, load } = images();
  const { queue } = queueOf(load);
  queue.load([pageImage(0)]);
  started[0]?.come(true);
  await callbacksRun();
  queue.load([pageImage(0, 3)]);
  started[1]?.come(true);
  await callbacksRun();
  expect(queue.imageAt(pageImage(0, 3).url)?.src).toBe(pageImage(0, 3).url);
  expect(queue.imageAt(pageImage(0).url)).toBeUndefined();
});

test("a page's image that fails is said once, and loads again only once it's asked for again", async () => {
  const { started, load, urls } = images();
  const { queue, done } = queueOf(load);
  queue.load([pageImage(0)]);
  started[0]?.come(false);
  await callbacksRun();
  expect(done).toEqual([[pageImage(0), false]]);
  expect(queue.imageAt(pageImage(0).url)).toBeUndefined();
  // Scrolled about: the page is wanted again and again, but waits out its retry.
  queue.load([pageImage(0)]);
  queue.load([pageImage(0)]);
  expect(urls()).toEqual([pageImage(0).url]);
  queue.retry(pageImage(0).url);
  expect(urls()).toEqual([pageImage(0).url, pageImage(0).url]);
});

/** An editor on a forty-page document, its first two pages near and in, each image asked for noted. */
async function editorOnFortyPages() {
  const doc = aDoc({ pages: Array.from({ length: 40 }, () => A4) });
  const stopped: string[] = [];
  vi.mocked(loadImage)
    .mockReset()
    .mockImplementation((url: string) => ({
      image: { src: url } as HTMLImageElement,
      // The screen's pages come at once; a far one takes a while to draw.
      loaded: /pages\/[01]\?/.test(url) ? Promise.resolve(true) : new Promise<boolean>(() => undefined),
      stop: () => stopped.push(url),
    }));
  const made = createEditor(new File(["%PDF-"], "contract.pdf"), doc, 2);
  editor = made;
  made.near.add(0);
  made.near.add(1);
  loadPageImages(made);
  await callbacksRun();
  vi.mocked(loadImage).mockClear();
  return { editor: made, doc, stopped, asked: () => vi.mocked(loadImage).mock.calls.map(([url]) => url) };
}

test("a sweep of the pointer down the page list loads nothing ahead; a rest on a tick does", async () => {
  const { editor, doc, asked } = await editorOnFortyPages();
  vi.useFakeTimers();
  for (let page = 2; page <= 20; page++) {
    loadAheadOnRest(editor, [page]);
    vi.advanceTimersByTime(IMAGE_INTENT_MS / 3);
  }
  expect(asked()).toEqual([]);
  vi.advanceTimersByTime(IMAGE_INTENT_MS);
  expect(asked()).toEqual([pageUrl(doc, 20, 2)]);
});

test("a page once on the way, the pointer gone since, isn't loaded again at a new scale", async () => {
  const { editor, doc, asked } = await editorOnFortyPages();
  loadAhead(editor, [30]);
  nothingAhead(editor);
  vi.mocked(loadImage).mockClear();
  zoomBy(editor, 1, { pixelRatio: 2 });
  // The screen's pages in at the new scale, leaving room for any other.
  await callbacksRun();
  const { scale } = editor.store.get();
  expect(asked()).not.toContain(pageUrl(doc, 30, scale));
});

test("Find's places, once it closes, aren't loaded again at a new scale", async () => {
  const { editor, doc, asked } = await editorOnFortyPages();
  openFind(editor);
  loadAhead(editor, [25, 35]);
  closeFind(editor);
  vi.mocked(loadImage).mockClear();
  zoomBy(editor, 1, { pixelRatio: 2 });
  // The screen's pages in at the new scale, leaving room for any other.
  await callbacksRun();
  const { scale } = editor.store.get();
  expect(asked().filter((url) => url === pageUrl(doc, 25, scale) || url === pageUrl(doc, 35, scale))).toEqual([]);
});

test("the pages near load nearest the page being read first, a deleted one by where it's drawn", async () => {
  const { editor, doc, asked } = await editorOnFortyPages();
  // Page 11 is being read; 10, left out of the download, is drawn in its place just above it.
  change(editor, { kind: "add", edits: [aDeletePage(10)] });
  editor.store.set({ pageInView: 11 });
  for (const page of [9, 10, 11, 12, 13]) editor.near.add(page);
  vi.mocked(loadImage).mockClear();
  loadPageImages(editor);
  expect(asked()).toEqual([pageUrl(doc, 11, 2), pageUrl(doc, 10, 2)]);
  expect(vi.mocked(loadImage).mock.calls[0]?.[1]).toEqual({ inView: true });
});
