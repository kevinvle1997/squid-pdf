import { beforeAll, describe, expect, test, vi } from "vitest";
import { renderStrips } from "../api/client";
import type { Render } from "../api/types";
import { aDoc, aFont, aSpan, halfWideGlyphs, SENTENCES } from "../fixtures";
import { type AmountValue, amountsIn } from "./amounts";
import { learnDateLanguages } from "./dateNames";
import { type CalendarDay, dateReadingOf, datesIn } from "./dates";
import { createEditor } from "./editor";
import { editableValueIn } from "./formattedValues";
import { edit, spanDraftOf, typeInto } from "./typing";
import {
  amountFormatOptions,
  chooseDate,
  markedDays,
  openValueEditor,
  reasonsAnyDayGives,
  shownDayAfter,
} from "./valueEditor";
import { fill } from "./words";

vi.mock(import("../api/client"), async (original) => ({ ...(await original()), renderStrips: vi.fn() }));

// Every language a date is read in, learnt in the background as an editor would: the dates here need them.
beforeAll(() => learnDateLanguages());

/** The one amount `text` holds. */
function amountIn(text: string): AmountValue {
  const [amount] = amountsIn(text);
  if (amount === undefined) throw new Error(`no amount: ${text}`);
  return amount;
}

describe("a day in the calendar", () => {
  test("is marked when its date needs a digit or letter the span's font lacks, in the server's short sentence", () => {
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
    // The letters alone: what draws the line instead is the slip's full verdict, above the calendar.
    expect(marked.get(17)).toBe("no 7 in this font");
    expect(marked.get(15)).toBe(fill(SENTENCES.not_in_font, { chars: ["5"] }));
    // The days it can write are left unmarked.
    expect(marked.has(14)).toBe(false);
    expect(marked.has(16)).toBe(false);
  });

  test.each([
    ["a broken email", "Due 14 March 2026, ask ann@example.com", "Due 14 March 2026, ask ann@example"],
    ["a letter the font lacks", "Due 14 March 2026", "Dué 14 March 2026"],
  ])("isn't marked for %s elsewhere in the line: the slip says that, and no date has it", (_, words, typed) => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const glyphs = halfWideGlyphs(`${words}0123456789`);
    const span = aSpan({ id: "due", text: words, room_pt: 400 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    typeInto(editor, typed);
    const [date] = datesIn(typed, dateReadingOf([words], { readerLanguages: [] }));
    if (date === undefined) throw new Error("no date");

    expect(markedDays(editor.store.get(), { value: date, text: typed, year: 2026, month: 3 }).size).toBe(0);
  });

  test("names only the letters its own date adds, beside one the line lacks already", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const words = "Due 14 March 2026";
    // No 5 and no 7, nor the é typed in front of the date.
    const span = aSpan({ id: "due", text: words, room_pt: 400 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(words) })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const typed = "Dué 14 March 2026";
    typeInto(editor, typed);
    const [date] = datesIn(typed, dateReadingOf([words], { readerLanguages: [] }));
    if (date === undefined) throw new Error("no date");

    const marked = markedDays(editor.store.get(), { value: date, text: typed, year: 2026, month: 3 });
    expect(marked.get(17)).toBe(fill(SENTENCES.not_in_font, { chars: ["7"] }));
    expect(marked.has(14)).toBe(false);
  });

  test("is marked for a date its own month can't fit in the line, not for a line long before it", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const words = "Due 14 May 2026";
    // Each letter 6 points wide: "September" is 36 points longer than "May", and the line has 12 to spare.
    const glyphs = halfWideGlyphs(`${words}abcdefghijklmnopqrstuvwxyzJFMASOND0123456789 `);
    const span = aSpan({ id: "due", text: words, room_pt: 12 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const [date] = datesIn(words, dateReadingOf([words], { readerLanguages: ["en"] }));
    if (date === undefined) throw new Error("no date");

    expect(markedDays(editor.store.get(), { value: date, text: words, year: 2026, month: 9 }).get(14)).toMatch(/\d+/);
    expect(markedDays(editor.store.get(), { value: date, text: words, year: 2026, month: 6 }).size).toBe(0);
    // Typed long before the date, the line is past its room whatever's chosen: the slip says so, the days don't.
    const long = "Due without fail and in full 14 May 2026";
    typeInto(editor, long);
    const [later] = datesIn(long, dateReadingOf([words], { readerLanguages: ["en"] }));
    if (later === undefined) throw new Error("no date");
    expect(markedDays(editor.store.get(), { value: later, text: long, year: 2026, month: 9 }).size).toBe(0);
  });

  test("isn't marked, least of all the day chosen, for words typed elsewhere, shorter than the date, that run the line past", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const words = "Due 14 May 2026";
    // Each letter 6 points wide, and the line has 12 to spare: " on the" runs it past, and is shorter than the date.
    const glyphs = halfWideGlyphs(`${words}abcdefghijklmnopqrstuvwxyzJFMASOND0123456789 `);
    const span = aSpan({ id: "due", text: words, room_pt: 12 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const typed = "Due on the 14 May 2026";
    typeInto(editor, typed);
    const [date] = datesIn(typed, dateReadingOf([words], { readerLanguages: ["en"] }));
    if (date === undefined) throw new Error("no date");

    // The line is past its room whatever's chosen: the slip says so, the days don't.
    const may = markedDays(editor.store.get(), { value: date, text: typed, year: 2026, month: 5 });
    expect(may.has(14)).toBe(false);
    expect(may.size).toBe(0);
  });

  test("can give no reason the calendar hasn't laid out, nor one longer, in the year open or another", () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    // A subset that drew only "Due 14 March 2026": most months' names and 2027 can't be written.
    const words = "Due 14 March 2026";
    const span = aSpan({ id: "due", text: words, room_pt: 400 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(words) })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    const [date] = datesIn(words, dateReadingOf([words], { readerLanguages: ["en"] }));
    if (date === undefined) throw new Error("no date");
    const state = editor.store.get();

    const reserved = reasonsAnyDayGives(state, { value: date, text: words, year: 2026 });
    const longest = Math.max(...reserved.map((why) => why.length));
    for (let month = 1; month <= 12; month++) {
      const thisYear = markedDays(state, { value: date, text: words, year: 2026, month });
      for (const why of thisYear.values()) expect(reserved).toContain(why);
      const nextYear = markedDays(state, { value: date, text: words, year: 2027, month });
      for (const why of nextYear.values()) expect(why.length).toBeLessThanOrEqual(longest);
    }
  });

  test("shows one reason at a time: a day's shown, then hidden only if it's still the one shown", () => {
    expect(shownDayAfter(null, { day: 17, isShown: true })).toBe(17);
    expect(shownDayAfter(17, { day: 19, isShown: true })).toBe(19);
    expect(shownDayAfter(19, { day: 17, isShown: false })).toBe(19);
    expect(shownDayAfter(19, { day: 19, isShown: false })).toBeNull();
  });
});

describe("the value its editor is open on", () => {
  test("broken a moment as it's typed, closes its editor, and is held again once it's back", async () => {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const words = "Due 14 March 2026, total 1.250,00 €";
    const span = aSpan({ id: "due", text: words, room_pt: 400 });
    const doc = aDoc({
      spans: [span],
      fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(`${words}0123456789`) })],
    });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "due", null);
    await vi.waitFor(() => expect(editor.store.get().dates).not.toBeNull());
    const { dates } = editor.store.get();
    /** The value its control is offered for, as the field shows it. */
    const offered = (text: string) =>
      editableValueIn(text, { dates, at: undefined, held: spanDraftOf(editor.store.get())?.value ?? null });
    expect(openValueEditor(editor, offered(words))).toBe(true);
    expect(offered(words)?.kind).toBe("date");
    // Opening, it takes focus as it's drawn; open already, focus is sent into it from the field.
    expect(editor.store.get().focusTo).toBeNull();
    expect(openValueEditor(editor, offered(words))).toBe(true);
    expect(editor.store.get().focusTo).toEqual({ valueEditor: "due" });

    // The year retyped by hand: "2026" is "202" a moment, then "2027".
    typeInto(editor, "Due 14 March 202, total 1.250,00 €");
    expect(spanDraftOf(editor.store.get())?.valueEditorOpen).toBe(false);
    // Closed under it, the editor sends focus back to the field: never left to fall to the page's body.
    expect(editor.store.get().focusTo).toEqual({ spanId: "due" });
    typeInto(editor, "Due 14 March 20, total 1.250,00 €");
    typeInto(editor, "Due 14 March 202, total 1.250,00 €");
    const retyped = "Due 14 March 2027, total 1.250,00 €";
    typeInto(editor, retyped);
    expect(offered(retyped)?.kind).toBe("date");
  });
});

describe("a date chosen twice in one edit", () => {
  /** An editor typing into `words`, and its date's editor opened on what the field reads, as Alt+Down does. */
  async function editing(words: string) {
    vi.mocked(renderStrips).mockReturnValue(new Promise<Render>(() => undefined));
    const span = aSpan({ id: "s", text: words, room_pt: 400 });
    const doc = aDoc({ spans: [span], fonts: [aFont("Times-Roman", { glyphs: halfWideGlyphs(words) })] });
    const editor = createEditor(new File(["%PDF-"], "invoice.pdf"), doc, 2);
    edit(editor, "s", null);
    await vi.waitFor(() => expect(editor.store.get().dates).not.toBeNull());
    const offered = () => {
      const state = editor.store.get();
      const held = spanDraftOf(state)?.value ?? null;
      return editableValueIn(spanDraftOf(state)?.text ?? "", { dates: state.dates, at: undefined, held });
    };
    /** Choose `date` in the date's editor, and what the field reads then. */
    const choose = (date: CalendarDay) => {
      const value = offered();
      if (value?.kind !== "date" || !openValueEditor(editor, value)) throw new Error("no date");
      chooseDate(editor, value, date);
      return spanDraftOf(editor.store.get())?.text;
    };
    return { editor, offered, choose };
  }

  test("keeps the form it was opened with: a French first of the month is “1er” again", async () => {
    const { choose } = await editing("Payé le 1er mars 2026");
    expect(choose({ year: 2026, month: 4, day: 2 })).toBe("Payé le 2 avril 2026");
    expect(choose({ year: 2026, month: 4, day: 1 })).toBe("Payé le 1er avril 2026");
  });

  test.each([
    ["Saturday, 14 March 2026", "16 March 2026", "17 March 2026"],
    ["Due 14 March 2026", "Due Monday, 16 March 2026", "Due Tuesday, 17 March 2026"],
    ["Due 14 March 2026", "Due 16 Mar 2026", "Due 17 Mar 2026"],
    ["Due 14 March 2026", "Due 2026-03-16", "Due 2026-03-17"],
  ])("written over by hand between the two, “%s” takes the form typed: “%s”", async (words, typed, comesTo) => {
    const { editor, choose } = await editing(words);
    choose({ year: 2026, month: 3, day: 16 });
    typeInto(editor, typed);
    expect(choose({ year: 2026, month: 3, day: 17 })).toBe(comesTo);
  });

  test("given a bracket that holds more than its weekday by hand, offers its editor no more", async () => {
    const { editor, offered, choose } = await editing("2026年3月14日(土)");
    expect(choose({ year: 2026, month: 3, day: 16 })).toBe("2026年3月16日(月)");
    typeInto(editor, "2026年3月16日(月・祝)");
    expect(offered()).toBeUndefined();
  });
});

describe("the amount's field", () => {
  test("shows the amount in its currency, where Intl knows it, to the page's decimals", () => {
    expect(amountFormatOptions(amountIn("1.250,00 €"))).toEqual({
      style: "currency",
      currency: "EUR",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    expect(amountFormatOptions(amountIn("KWD 1.250"))).toMatchObject({ currency: "KWD", maximumFractionDigits: 3 });
  });
});
