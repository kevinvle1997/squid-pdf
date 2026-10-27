import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProblemError } from "../api/client";
import type { Edit, ImageInfo, Render, RenderBody } from "../api/types";
import {
  aDoc,
  aFit,
  aFont,
  anInsert,
  aProblem,
  aRedact,
  aRedaction,
  aReplace,
  aReply,
  aSkipped,
  aSpan,
  aSpanNotice,
  halfWideGlyphs,
} from "../fixtures";
import { layoutOf } from "./editor";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { newTextVerdictOf } from "./newText";
import { project, UNEDITED } from "./project";
import { type Drawn, landedFitOf, landedFits, landedNewTextFitOf, NOTHING_DRAWN, RenderQueue } from "./render";

const ONE = aSpan({ id: "one", page: 0 });
const TWO = aSpan({ id: "two", page: 1 });
const DOC = aDoc({ spans: [ONE, TWO] });
const readingOf = (...edits: Edit[]) => project(DOC, entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));
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

/** A queue asking the fake server, with `near` the pages near the screen. */
function aQueue(near: ReadonlySet<number> = new Set()) {
  return new RenderQueue({
    reopener: { doc: DOC, withDocument: (use) => use(DOC) },
    scale: 2,
    drawn: (now) => drawn.push(now),
    render: fake.render,
    // A strip's address is its image as sent: these tests don't paint.
    load: async (image) => image.image,
    near,
  });
}

beforeEach(() => {
  drawn = [];
  fake = server();
  queue = aQueue();
});

// Each span's strip, as asked for: two points past its box either side.
const shown = (page: number) => ({ page, y: 98, y1: 114, src: `page ${page}` });

const settle = () => new Promise((resolve) => setTimeout(resolve));

describe("the render queue", () => {
  test("it asks for a strip around each edited row, sending the edits as they read; fits land with their page", async () => {
    const reading = readingOf(aReplace(ONE.id));
    queue.draw(reading);
    await settle();
    expect(fake.asked).toHaveLength(1);
    expect(fake.asked[0]?.body).toEqual({
      edits: [aReplace(ONE.id)],
      scale: 2,
      regions: [{ page: 0, y0: 98, y1: 114 }],
    });

    fake.asked[0]?.answer(aReply({ images: [stripFor(0)], fits: { [ONE.id]: aFit() } }));
    await settle();
    expect(queue.drawn.strips.get(0)).toEqual([shown(0)]);
    expect(queue.drawn.from.get(0)).toBe(reading);
    expect(queue.drawn.fits.get(0)).toEqual({ [ONE.id]: aFit() });
  });

  test("a redaction's verdict lands with its page, and goes with it", async () => {
    const redaction = aRedact(TWO.id);
    queue.draw(readingOf(redaction));
    await settle();
    const verdict = aRedaction({ verified: false, message: "This text couldn't be removed." });
    fake.asked[0]?.answer(aReply({ images: [stripFor(1)], redactions: { [TWO.id]: verdict } }));
    await settle();
    expect(queue.drawn.redactions.get(1)).toEqual({ [TWO.id]: verdict });

    queue.draw(UNEDITED);
    await settle();
    expect(queue.drawn.redactions.has(1)).toBe(false);
  });

  test("a newer change takes over the pages an older request was still drawing", async () => {
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    const newer = readingOf(aReplace(ONE.id), aReplace(TWO.id));
    queue.draw(newer);
    await settle();
    expect(fake.asked[0]?.signal.aborted).toBe(true);
    expect(fake.asked[1]?.body.regions.map((region) => region.page)).toEqual([0, 1]);

    fake.asked[1]?.answer(fake.reply([0, 1]));
    await settle();
    expect([...queue.drawn.from.keys()].sort()).toEqual([0, 1]);
    expect(queue.drawn.failed).toBeNull();
  });

  test("the pages near the screen are asked for first, on their own, then the rest", async () => {
    const near = new Set([1]);
    queue = aQueue(near);
    const askedFor = () => fake.asked.map(({ body }) => body.regions.map((region) => region.page));
    queue.draw(readingOf(aReplace(ONE.id), aReplace(TWO.id)));
    await settle();
    expect(askedFor()).toEqual([[1]]);

    fake.asked[0]?.answer(fake.reply([1]));
    await settle();
    expect(queue.drawn.strips.get(1)).toEqual([shown(1)]);
    expect(askedFor()).toEqual([[1], [0]]);
    fake.asked[1]?.answer(fake.reply([0]));
    await settle();
    expect(queue.drawn.strips.get(0)).toEqual([shown(0)]);
    expect(fake.asked).toHaveLength(2);
  });

  test("a page with nothing left on it goes back to its image at once, without asking", async () => {
    queue.draw(readingOf(aReplace(ONE.id)));
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
      load: async (image) => {
        await decoding;
        return image.image;
      },
    });
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    fake.asked[0]?.answer(fake.reply([0]));
    await settle();
    expect(drawn).toEqual([]);
    decoded();
    await settle();
    expect(drawn).toHaveLength(1);
  });

  test("a failed render says why until one lands, and its page is asked for again with the next change", async () => {
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    fake.asked[0]?.fail(new ProblemError(aProblem(0, "Couldn't reach the server.")));
    await settle();
    expect(queue.drawn.failed).toBe("Couldn't reach the server.");
    expect(queue.drawn.from.has(0)).toBe(false);

    queue.draw(readingOf(aReplace(ONE.id), aReplace(TWO.id)));
    await settle();
    expect(fake.asked[1]?.body.regions.map((region) => region.page)).toEqual([0, 1]);
    fake.asked[1]?.answer(fake.reply([0, 1]));
    await settle();
    expect(queue.drawn.failed).toBeNull();
  });

  test("a bug while rendering is said and logged, not thrown, and its page stays stale", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    fake.asked[0]?.fail(new TypeError("reply.images is undefined"));
    await settle();
    expect(queue.drawn.failed).not.toBeNull();
    expect(logged).toHaveBeenCalledWith(new TypeError("reply.images is undefined"));
    expect(queue.drawn.from.has(0)).toBe(false);
  });

  test("what a reply says of a page goes with its strips; what it left out is of the whole list", async () => {
    const drewOtherwise = aSpanNotice("Drawn in Liberation Serif.", ONE.id);
    const leftOut = aSkipped(0, "An edit points at nothing.");
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    fake.asked[0]?.answer(aReply({ images: [stripFor(0)], notices: [drewOtherwise], skipped: [leftOut] }));
    await settle();
    expect(queue.drawn.notices.get(0)).toEqual([drewOtherwise]);
    expect(queue.drawn.skipped).toEqual([leftOut]);

    // Only page 2 is drawn again: page 1 keeps what was said of it.
    queue.draw(readingOf(aReplace(ONE.id), aReplace(TWO.id)));
    await settle();
    fake.asked[1]?.answer(fake.reply([1]));
    await settle();
    expect(queue.drawn.notices.get(0)).toEqual([drewOtherwise]);
    expect(queue.drawn.skipped).toEqual([]);

    queue.draw(UNEDITED);
    await settle();
    expect(queue.drawn.notices.size).toBe(0);
  });

  test("stopped, what's in flight is dropped, and nothing is asked for again", async () => {
    queue.draw(readingOf(aReplace(ONE.id)));
    await settle();
    queue.stop();
    expect(fake.asked[0]?.signal.aborted).toBe(true);
    queue.retry();
    await settle();
    expect(fake.asked).toHaveLength(1);
    expect(drawn).toEqual([]);
  });

  test("retry asks again for what's still stale, and nothing once it's all drawn", async () => {
    queue.draw(readingOf(aReplace(ONE.id)));
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

describe("a fit for the words a span reads now", () => {
  const drawnFrom = readingOf(aReplace(ONE.id, "is one"));
  const fit = aFit({ fidelity: "substitute", substitute: "Liberation Serif Regular" });
  const drawnHere: Drawn = { ...NOTHING_DRAWN, from: new Map([[0, drawnFrom]]), fits: new Map([[0, { one: fit }]]) };

  test("counts once a render of those very words has landed", () => {
    const now = drawnFrom.spans.get(ONE.id);
    expect(now === undefined ? undefined : landedFitOf(drawnHere, now)).toBe(fit);
    const fits = { one: fit };
    expect(landedFits(fits, { now: drawnFrom.pages.get(0), drawnFrom: drawnFrom.pages.get(0) })).toBe(fits);
  });

  test("says nothing of other words typed since, until their render lands", () => {
    const typedSince = readingOf(aReplace(ONE.id, "was one, now"));
    const now = typedSince.spans.get(ONE.id);
    expect(now === undefined ? fit : landedFitOf(drawnHere, now)).toBeUndefined();
    const fits = { one: fit };
    expect(landedFits(fits, { now: typedSince.pages.get(0), drawnFrom: drawnFrom.pages.get(0) })).toEqual({});
  });
});

describe("new text's fit", () => {
  test("lands under the version it was drawn from, and its verdict replaces the one worked out live", async () => {
    // The page's font draws it, as the browser works it out: its render says a similar font drew it after all.
    const text = anInsert({ font: ONE.font, text: "Paid €" });
    const reading = readingOf(aReplace(ONE.id), text);
    queue.draw(reading);
    await settle();
    const drew = aFit({ fidelity: "substitute", substitute: "Liberation Serif Regular", missing: ["€"] });
    fake.asked[0]?.answer(aReply({ images: [stripFor(0)], insert_fits: [{ ...drew, edit: 1 }] }));
    await settle();

    const [now] = reading.pages.get(0)?.newTexts ?? [];
    if (now === undefined) throw new Error("the new text reads on its page");
    expect(landedNewTextFitOf(queue.drawn, now)).toEqual(drew);
    const layout = layoutOf(aDoc({ spans: [ONE], fonts: [aFont(ONE.font, { glyphs: halfWideGlyphs("Paid €") })] }));
    expect(newTextVerdictOf(layout, { insert: text, fit: undefined }).fidelity).toBe("exact");
    expect(newTextVerdictOf(layout, { insert: text, fit: landedNewTextFitOf(queue.drawn, now) })).toEqual(drew);
  });
});
