import { describe, expect, test } from "vitest";
import type { Edit, FitInfo } from "../api/types";
import {
  aDoc,
  aFit,
  aFont,
  anInsert,
  aRedact,
  aReplace,
  aSkipped,
  aSpan,
  aSpanNotice,
  halfWideGlyphs,
  RULES,
} from "../fixtures";
import { EMPTY_HISTORY, entriesOf, historyReducer } from "./history";
import { disagreementsAfter, NO_DISAGREEMENTS, type PreviewDisagreement } from "./previewDisagreements";
import { project, type Reading } from "./project";
import { type Drawn, NOTHING_DRAWN } from "./render";

const ONE = aSpan({ id: "one", page: 0 });
const TWO = aSpan({ id: "two", page: 1 });
// Every letter these tests type, as wide as each other: "now" is shorter than "was one", so it fits.
const TIMES = aFont("Times-Roman", { glyphs: halfWideGlyphs("wasonetu ") });
const FONTS = new Map([[TIMES.name, TIMES]]);

const readingOf = (...edits: Edit[]) =>
  project(aDoc({ spans: [ONE, TWO] }), entriesOf(historyReducer(EMPTY_HISTORY, { kind: "add", edits })));

/** `reading` drawn on `pages`, with the server's reply for each span. */
function drawnOf(reading: Reading, pages: number[], reply: Partial<Drawn> = {}): Drawn {
  return { ...NOTHING_DRAWN, from: new Map(pages.map((page) => [page, reading])), ...reply };
}
const fitsOn = (page: number, fits: Record<string, FitInfo>) => new Map([[page, fits]]);

const judged = (disagreements: ReadonlyMap<number, PreviewDisagreement> = NO_DISAGREEMENTS) => ({
  layout: { fonts: FONTS, spans: new Map() },
  rules: RULES,
  disagreements,
});
const pulsing = (disagreements: ReadonlyMap<number, PreviewDisagreement>, page = 0) => [
  ...(disagreements.get(page)?.spanIds ?? []),
];

describe("where a render disagrees with its preview", () => {
  test("a render that says what the live check did swaps in silently", () => {
    const reading = readingOf(aReplace(ONE.id));
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12 }) }) });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual([]);
  });

  test("letters the server found lacking, which the live check didn't, pulse", () => {
    const reading = readingOf(aReplace(ONE.id));
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12, missing: ["n"] }) }) });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a line the server finds too long, where the live check had it fit, pulses", () => {
    const reading = readingOf(aReplace(ONE.id));
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: 30 }) }) });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a notice or a skip naming the span pulses: the browser never foresees one", () => {
    const reading = readingOf(aReplace(ONE.id));
    const fits = fitsOn(0, { one: aFit({ delta_pt: -12 }) });
    const noticed = drawnOf(reading, [0], { fits, notices: new Map([[0, [aSpanNotice("went in otherwise", "one")]]]) });
    const skipped = drawnOf(reading, [0], { fits, skipped: [aSkipped(0, "left out")] });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, noticed, judged()))).toEqual(["one"]);
    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, skipped, judged()))).toEqual(["one"]);
  });

  test("a way out the server drew other than the one asked pulses", () => {
    const reading = readingOf(aReplace(ONE.id, "now", { strategy: "shrink" }));
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: aFit({ delta_pt: -12, strategy: "as-is" }) }) });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a verdict other than the one worked out as it was typed pulses, as the chip said otherwise", () => {
    const reading = readingOf(aReplace(ONE.id));
    // Its own font draws every letter typed, so the chip said it looks identical; the page refused the font.
    const similar = aFit({ delta_pt: -12, fidelity: "substitute", substitute: "Liberation Serif Regular" });
    const after = drawnOf(reading, [0], { fits: fitsOn(0, { one: similar }) });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual(["one"]);
  });

  test("a redaction never pulses: it previews bare, as it's drawn", () => {
    const reading = readingOf(aRedact(ONE.id));
    const after = drawnOf(reading, [0], { skipped: [aSkipped(0, "left out")] });

    expect(pulsing(disagreementsAfter(NOTHING_DRAWN, after, judged()))).toEqual([]);
  });

  test("only what was previewed pulses: a span drawn from the same words as before stays still", () => {
    const lacking = { fits: fitsOn(0, { one: aFit({ delta_pt: -12, missing: ["n"] }) }) };
    const before = drawnOf(readingOf(aReplace(ONE.id)), [0], lacking);
    const again = drawnOf(readingOf(aReplace(ONE.id)), [0], lacking);

    expect(pulsing(disagreementsAfter(before, again, judged()))).toEqual([]);
  });

  test("a page the landing didn't draw keeps its pulse, and a span that disagrees again pulses again", () => {
    const lacking = (spanId: string) => ({ [spanId]: aFit({ delta_pt: -12, missing: ["n"] }) });
    const first = readingOf(aReplace(ONE.id), aReplace(TWO.id));
    const both = drawnOf(first, [0, 1], {
      fits: new Map([
        [0, lacking("one")],
        [1, lacking("two")],
      ]),
    });
    const once = disagreementsAfter(NOTHING_DRAWN, both, judged());
    // Only the first page changes, and only it is drawn again.
    const next = readingOf(aReplace(ONE.id, "nous"), aReplace(TWO.id));
    const twice = disagreementsAfter(
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

describe("where a render of new text disagrees with its preview", () => {
  const signed = anInsert({ font: "Caveat Bold", text: "Signed" });
  const textPulsing = (disagreements: ReadonlyMap<number, PreviewDisagreement>) => [
    ...(disagreements.get(0)?.newTextIds ?? []),
  ];

  test("in a face we ship, drawn as asked, it swaps in silently; a letter left out the live check drew pulses", () => {
    const reading = readingOf(signed);
    const [text] = reading.pages.get(0)?.newTexts ?? [];
    const drewAs = (fit: FitInfo) => drawnOf(reading, [0], { newTextFits: new Map([[0, { [text?.id ?? 0]: fit }]]) });

    expect(textPulsing(disagreementsAfter(NOTHING_DRAWN, drewAs(aFit()), judged()))).toEqual([]);
    const leftOut = drewAs(aFit({ left_out: ["S"] }));
    expect(textPulsing(disagreementsAfter(NOTHING_DRAWN, leftOut, judged()))).toEqual([text?.newTextId]);
  });
});
