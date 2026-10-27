import { describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../fixtures";
import { dateReadingOf, datesIn } from "./dates";
import { createEditor } from "./editor";
import { edit } from "./typing";
import { markedDays } from "./valueEditor";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

describe("a day in the calendar", () => {
  test("is marked when its date needs a digit or letter the span's font lacks, with the server's sentence", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    // A subset that drew only "Due 14 March 2026": no 5 and no 7.
    const glyphs = halfWideGlyphs("Due 14 March 2026");
    const span = aSpan({ id: "due", text: "Due 14 March 2026", room_pt: 100 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const [date] = datesIn(span.text, dateReadingOf([span.text], { readerLanguages: [] }));
    if (date === undefined) throw new Error("no date");

    const marked = markedDays(editor.store.get(), { value: date, text: span.text, year: 2026, month: 3 });
    const noSeven = fill(SENTENCES.missing, { chars: ["7"], font: "Liberation Serif Regular" });
    expect(marked.get(17)).toBe(noSeven);
    expect(marked.get(15)).toBe(fill(SENTENCES.missing, { chars: ["5"], font: "Liberation Serif Regular" }));
    // The days it can write are left unmarked.
    expect(marked.has(14)).toBe(false);
    expect(marked.has(16)).toBe(false);
  });
});
