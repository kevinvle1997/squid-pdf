import { describe, expect, test } from "vitest";
import type { Edit, FitInfo } from "../api/types";
import { aFit, aFont, aSkipped, aSpan, aSpanNotice, RULES } from "../fixtures";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { project, type Reading } from "./project";
import { type Drawn, NOTHING_DRAWN } from "./render";
import { NOTHING_SETTLING, type Settling, settlingAfter } from "./settle";

const ONE = aSpan({ id: "one", page: 0 });
const TWO = aSpan({ id: "two", page: 1 });
// Every letter these tests type, as wide as each other: "now" is shorter than "was one", so it fits.
const TIMES = aFont("Times-Roman", { glyphs: Object.fromEntries([..."wasonetu "].map((letter) => [letter, 500])) });
const FONTS = new Map([[TIMES.name, TIMES]]);

const readingOf = (...edits: Edit[]) =>
  project([ONE, TWO], entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));
const replace = (span = ONE, text = "now"): Edit => ({ kind: "replace", span_id: span.id, text });

/** `reading` drawn on `pages`, with what the server said of each span. */
function drawnOf(reading: Reading, pages: number[], said: Partial<Drawn> = {}): Drawn {
  return { ...NOTHING_DRAWN, from: new Map(pages.map((page) => [page, reading])), ...said };
}
const fitsOn = (page: number, fits: Record<string, FitInfo>) => new Map([[page, fits]]);

const judged = (settling: ReadonlyMap<number, Settling> = NOTHING_SETTLING) => ({
  fonts: FONTS,
  rules: RULES,
  settling,
});
const pulsing = (settling: ReadonlyMap<number, Settling>, page = 0) => [...(settling.get(page)?.spanIds ?? [])];

describe("the settle", () => {
  test("a render that says what the live check did swaps in silently", () => {
    const reading = readingOf(replace());
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12 }) }) });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, after, judged()))).toEqual([]);
  });

  test("letters the server found lacking, which the live check didn't, pulse", () => {
    const reading = readingOf(replace());
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12, missing: ["n"] }) }) });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a line the server finds too long, where the live check had it fit, pulses", () => {
    const reading = readingOf(replace());
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: 30 }) }) });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a notice or a skip naming the span pulses: the browser never foresees one", () => {
    const reading = readingOf(replace());
    const fits = fitsOn(0, { one: aFit({ delta_pt: -12 }) });
    const noticed = drawnOf(reading, [0], { fits, notices: new Map([[0, [aSpanNotice("went in otherwise", "one")]]]) });
    const skipped = drawnOf(reading, [0], { fits, skipped: [aSkipped(0, "left out")] });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, noticed, judged()))).toEqual(["one"]);
    expect(pulsing(settlingAfter(NOTHING_DRAWN, skipped, judged()))).toEqual(["one"]);
  });

  test("a way out the server drew other than the one asked pulses", () => {
    const reading = readingOf({ kind: "replace", span_id: ONE.id, text: "now", strategy: "shrink" });
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12, strategy: "as-is" }) }) });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a redaction never pulses: it previews bare, as it's drawn", () => {
    const reading = readingOf({ kind: "redact", span_id: ONE.id });
    const after = drawnOf(reading, [0], { skipped: [aSkipped(0, "left out")] });

    expect(pulsing(settlingAfter(NOTHING_DRAWN, after, judged()))).toEqual([]);
  });

  test("only what was previewed pulses: a span drawn from the same words as before stays still", () => {
    const lacking = { fits: fitsOn(0, { one: aFit({ delta_pt: -12, missing: ["n"] }) }) };
    const before = drawnOf(readingOf(replace()), [0], lacking);
    const again = drawnOf(readingOf(replace()), [0], lacking);

    expect(pulsing(settlingAfter(before, again, judged()))).toEqual([]);
  });

  test("a page the landing didn't draw keeps its pulse, and a span that disagrees again pulses again", () => {
    const lacking = (spanId: string) => ({ [spanId]: aFit({ delta_pt: -12, missing: ["n"] }) });
    const first = readingOf(replace(ONE), replace(TWO));
    const both = drawnOf(first, [0, 1], {
      fits: new Map([
        [0, lacking("one")],
        [1, lacking("two")],
      ]),
    });
    const once = settlingAfter(NOTHING_DRAWN, both, judged());
    // Only the first page changes, and only it is drawn again.
    const next = readingOf(replace(ONE, "nous"), replace(TWO));
    const twice = settlingAfter(
      both,
      {
        ...both,
        from: new Map([
          [0, next],
          [1, first],
        ]),
      },
      judged(once),
    );

    expect(once.get(0)?.count).toBe(1);
    expect(twice.get(0)?.count).toBe(2);
    expect(twice.get(1)).toBe(once.get(1));
  });
});
