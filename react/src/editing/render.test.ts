import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError } from "../api/client";
import type { Document, Edit, ImageInfo, ProblemInfo, Render, RenderBody, SpanInfo } from "../api/types";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { UNEDITED, project } from "./project";
import { type Drawn, RenderQueue } from "./render";

const spanOn = (page: number, y0: number): SpanInfo => ({
  id: `p${page}-${y0}`,
  page,
  text: "was",
  font: "f",
  size: 10,
  color: [0, 0, 0],
  bbox: { x0: 72, y0, x1: 100, y1: y0 + 12 },
  origin: [72, y0 + 10],
  fidelity: "exact",
});
const ONE = spanOn(0, 100);
const TWO = spanOn(1, 100);
const DOC = {
  id: "doc",
  pages: [0, 1].map(() => ({ width: 595, height: 842, rotation: 0 })),
  spans: [ONE, TWO],
} as unknown as Document;
const readingOf = (...edits: Edit[]) =>
  project(DOC.spans, entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));
const replace = (span: SpanInfo, text = "now"): Edit => ({ kind: "replace", span_id: span.id, text });
const stripFor = (page: number): ImageInfo => ({ page, y: 98, image: `page ${page}` });

/** The server, answering only when a test says so. */
function server() {
  const asked: { body: RenderBody; signal: AbortSignal; answer: (reply: Render) => void; fail: (error: unknown) => void }[] = [];
  const render = vi.fn(
    (_id: string, body: RenderBody, signal: AbortSignal) =>
      new Promise<Render>((answer, fail) => {
        asked.push({ body, signal, answer, fail });
        signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")));
      }),
  );
  const reply = (pages: number[]): Render => ({ images: pages.map(stripFor), fits: {} }) as unknown as Render;
  return { asked, render, reply };
}

let drawn: Drawn[];
let failures: string[];
let fake: ReturnType<typeof server>;
let queue: RenderQueue;

beforeEach(() => {
  drawn = [];
  failures = [];
  fake = server();
  queue = new RenderQueue({
    reopener: { doc: DOC, withDocument: (use) => use(DOC) },
    scale: 2,
    drawn: (now) => drawn.push(now),
    failed: (detail) => failures.push(detail),
    render: fake.render,
    decode: async () => undefined,
  });
});

const settle = () => new Promise((resolve) => setTimeout(resolve));

describe("the render queue", () => {
  test("it asks for a strip around each edited row, sending the edits as they read", async () => {
    const view = readingOf(replace(ONE));
    queue.draw(view);
    await settle();
    expect(fake.asked).toHaveLength(1);
    expect(fake.asked[0]?.body).toEqual({ edits: [replace(ONE)], scale: 2, regions: [{ page: 0, y0: 98, y1: 114 }] });

    fake.asked[0]?.answer(fake.reply([0]));
    await settle();
    expect(queue.drawn.strips.get(0)).toEqual([stripFor(0)]);
    expect(queue.drawn.from.get(0)).toBe(view);
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
    expect(failures).toEqual([]);
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
    expect(queue.drawn.from.get(0)).toBe(UNEDITED);
  });

  test("strips wait for their images to decode, so the preview never gives way to nothing", async () => {
    let decoded = () => undefined as void;
    const decoding = new Promise<void>((resolve) => (decoded = resolve));
    queue = new RenderQueue({
      reopener: { doc: DOC, withDocument: (use) => use(DOC) },
      scale: 2,
      drawn: (now) => drawn.push(now),
      failed: (detail) => failures.push(detail),
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

  test("a failed render says why, and its page is asked for again with the next change", async () => {
    const problem = { type: "t", status: 0, detail: "Couldn't reach the server.", code: "unreachable", params: {} };
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.fail(new ProblemError(problem as ProblemInfo));
    await settle();
    expect(failures).toEqual(["Couldn't reach the server."]);
    expect(queue.drawn.from.has(0)).toBe(false);

    queue.draw(readingOf(replace(ONE), replace(TWO)));
    await settle();
    expect(fake.asked[1]?.body.regions.map((region) => region.page)).toEqual([0, 1]);
  });

  test("retry asks again for what's still stale, and nothing once it's all drawn", async () => {
    queue.draw(readingOf(replace(ONE)));
    await settle();
    fake.asked[0]?.fail(new ProblemError({ status: 0, detail: "offline" } as ProblemInfo));
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
