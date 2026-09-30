import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError } from "../api/client";
import type { Edit, ImageInfo, Render, RenderBody, SpanInfo } from "../api/types";
import { aDoc, aFit, aNotice, aProblem, aReply, aSkipped, aSpan } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { project, UNEDITED } from "./project";
import { type Drawn, RenderQueue } from "./render";

const ONE = aSpan({ id: "one", page: 0 });
const TWO = aSpan({ id: "two", page: 1 });
const DOC = aDoc({ spans: [ONE, TWO] });
const readingOf = (...edits: Edit[]) =>
  project(DOC.spans, entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));
const replace = (span: SpanInfo, text = "now"): Edit => ({ kind: "replace", span_id: span.id, text });
const stripFor = (page: number): ImageInfo => ({ page, y: 98, image: `page ${page}` });

/** The server, answering only when a test says so. */
function server() {
  const asked: {
    body: RenderBody;
    signal: AbortSignal;
    answer: (reply: Render) => void;
    fail: (error: unknown) => void;
  }[] = [];
  const render = vi.fn(
    (_id: string, body: RenderBody, signal: AbortSignal) =>
      new Promise<Render>((answer, fail) => {
        asked.push({ body, signal, answer, fail });
        signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")));
      }),
  );
  const reply = (pages: number[]): Render => aReply({ images: pages.map(stripFor) });
  return { asked, render, reply };
}

let drawn: Drawn[];
let fake: ReturnType<typeof server>;
let queue: RenderQueue;

beforeEach(() => {
  drawn = [];
  fake = server();
  queue = new RenderQueue({
    reopener: { doc: DOC, withDocument: (use) => use(DOC) },
    scale: 2,
    drawn: (now) => drawn.push(now),
    render: fake.render,
    decode: async () => undefined,
  });
});

const settle = () => new Promise((resolve) => setTimeout(resolve));

describe("the render queue", () => {
  test("it asks for a strip around each edited row, sending the edits as they read; fits land with their page", async () => {
    const view = readingOf(replace(ONE));
    queue.draw(view);
    await settle();
    expect(fake.asked).toHaveLength(1);
    expect(fake.asked[0]?.body).toEqual({ edits: [replace(ONE)], scale: 2, regions: [{ page: 0, y0: 98, y1: 114 }] });

    fake.asked[0]?.answer(aReply({ images: [stripFor(0)], fits: { [ONE.id]: aFit() } }));
    await settle();
    expect(queue.drawn.strips.get(0)).toEqual([stripFor(0)]);
    expect(queue.drawn.from.get(0)).toBe(view);
    expect(queue.drawn.fits.get(0)).toEqual({ [ONE.id]: aFit() });
  });

  test("a newer change takes over the pages an older request was still drawing", async () => {
    queue.draw(readingOf(replace(ONE)));
    await settle();
    const newer = readingOf(replace(ONE), replace(TWO));
    queue.draw(newer);
    await settle();
    expect(fake.asked[0]?.signal.aborted).toBe(true);
    expect(fake.asked[1]?.body.regions.map((region) => region.page)).toEqual([0, 1]);

    fake.asked[1]?.answer(fake.reply([0, 1]));
    await settle();
    expect([...queue.drawn.from.keys()].sort()).toEqual([0, 1]);
    expect(queue.drawn.failed).toBeNull();
  });

  test("a page with nothing left on it goes back to its image at once, without asking", async () => {
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.answer(fake.reply([0]));
    await settle();

    queue.draw(UNEDITED);
    await settle();
    expect(fake.asked).toHaveLength(1);
    expect(queue.drawn.strips.has(0)).toBe(false);
    expect(queue.drawn.fits.has(0)).toBe(false);
    expect(queue.drawn.from.get(0)).toBe(UNEDITED);
  });

  test("strips wait for their images to decode, so the preview never gives way to nothing", async () => {
    let decoded: () => void = () => undefined;
    const decoding = new Promise<void>((resolve) => (decoded = resolve));
    queue = new RenderQueue({
      reopener: { doc: DOC, withDocument: (use) => use(DOC) },
      scale: 2,
      drawn: (now) => drawn.push(now),
      render: fake.render,
      decode: () => decoding,
    });
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.answer(fake.reply([0]));
    await settle();
    expect(drawn).toEqual([]);
    decoded();
    await settle();
    expect(drawn).toHaveLength(1);
  });

  test("a failed render says why until one lands, and its page is asked for again with the next change", async () => {
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.fail(new ProblemError(aProblem(0, "Couldn't reach the server.")));
    await settle();
    expect(queue.drawn.failed).toBe("Couldn't reach the server.");
    expect(queue.drawn.from.has(0)).toBe(false);

    queue.draw(readingOf(replace(ONE), replace(TWO)));
    await settle();
    expect(fake.asked[1]?.body.regions.map((region) => region.page)).toEqual([0, 1]);
    fake.asked[1]?.answer(fake.reply([0, 1]));
    await settle();
    expect(queue.drawn.failed).toBeNull();
  });

  test("a bug while rendering is said and logged, not thrown, and its page stays stale", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.fail(new TypeError("reply.images is undefined"));
    await settle();
    expect(queue.drawn.failed).not.toBeNull();
    expect(logged).toHaveBeenCalledWith(new TypeError("reply.images is undefined"));
    expect(queue.drawn.from.has(0)).toBe(false);
  });

  test("what a reply says of a page goes with its strips; what it left out is of the whole list", async () => {
    const drewOtherwise = aNotice("Drawn in Liberation Serif.", { span_id: ONE.id });
    const leftOut = aSkipped(0, "An edit points at nothing.");
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.answer(aReply({ images: [stripFor(0)], notices: [drewOtherwise], skipped: [leftOut] }));
    await settle();
    expect(queue.drawn.notices.get(0)).toEqual([drewOtherwise]);
    expect(queue.drawn.skipped).toEqual([leftOut]);

    // Only page 2 is drawn again: page 1 keeps what was said of it.
    queue.draw(readingOf(replace(ONE), replace(TWO)));
    await settle();
    fake.asked[1]?.answer(fake.reply([1]));
    await settle();
    expect(queue.drawn.notices.get(0)).toEqual([drewOtherwise]);
    expect(queue.drawn.skipped).toEqual([]);

    queue.draw(UNEDITED);
    await settle();
    expect(queue.drawn.notices.size).toBe(0);
  });

  test("retry asks again for what's still stale, and nothing once it's all drawn", async () => {
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.fail(new ProblemError(aProblem(0, "offline")));
    await settle();
    queue.retry();
    await settle();
    expect(fake.asked).toHaveLength(2);

    fake.asked[1]?.answer(fake.reply([0]));
    await settle();
    queue.retry();
    await settle();
    expect(fake.asked).toHaveLength(2);
  });
});
