import { beforeAll, describe, expect, test } from "vitest";
import { learnEveryLanguage } from "./dateNames";
import { type DateReading, type DateValue, dateReadingOf, datesIn, withDate } from "./dates";

// Every language Intl has dates for, learnt in the background as an editor would: the dates here need them.
beforeAll(() => learnEveryLanguage());

/** How the dates of a document of `texts` read, for a reader of `readerLanguages`. */
function readingOf(texts: string[], readerLanguages: string[] = []): DateReading {
  return dateReadingOf(texts, { readerLanguages });
}

/** The one date `text` holds, read as `reading` settles it; none where it doesn't. */
function dateIn(text: string, reading: DateReading): DateValue | undefined {
  const [date, ...more] = datesIn(text, reading);
  expect(more).toEqual([]);
  return date;
}

/** `text`, its one date changed to the 2nd of `month` of `year`, or undefined where no date is read. */
function asSecondOf(
  text: string,
  { month, year = 2026, reading }: { month: number; year?: number; reading: DateReading },
) {
  const date = dateIn(text, reading);
  return date === undefined ? undefined : withDate(text, date, { year, month, day: 2 });
}

describe("a month name several languages share", () => {
  test("is written in the reader's language where they'd write the new month differently", () => {
    const english = readingOf([], ["en-US", "en"]);
    expect(asSecondOf("14 April 2026", { month: 5, reading: english })).toBe("2 May 2026");
    expect(asSecondOf("14 September 2026", { month: 10, reading: english })).toBe("2 October 2026");
    expect(asSecondOf("14 November 2026", { month: 12, reading: english })).toBe("2 December 2026");
    expect(asSecondOf("14 Sep 2026", { month: 10, reading: english })).toBe("2 Oct 2026");
    const swedish = readingOf([], ["sv-SE"]);
    expect(asSecondOf("14 maj 2026", { month: 6, reading: swedish })).toBe("2 juni 2026");
    expect(asSecondOf("14 mars 2026", { month: 4, reading: swedish })).toBe("2 april 2026");
    expect(asSecondOf("14 januari 2026", { month: 2, reading: swedish })).toBe("2 februari 2026");
    expect(asSecondOf("14 novembre 2026", { month: 12, reading: readingOf([], ["it-IT"]) })).toBe("2 dicembre 2026");
    const spanish = readingOf([], ["es-ES"]);
    expect(asSecondOf("14 may 2026", { month: 6, reading: spanish })).toBe("2 jun 2026");
    expect(asSecondOf("14 mar 2026", { month: 4, reading: spanish })).toBe("2 abr 2026");
    expect(asSecondOf("14 feb 2026", { month: 3, reading: spanish })).toBe("2 mar 2026");
    expect(asSecondOf("14 Mai 2026", { month: 6, reading: readingOf([], ["de-DE"]) })).toBe("2 Juni 2026");
  });

  test("is written in the language the document's other dates settle, before the reader's", () => {
    const reading = readingOf(["Signed 2 januari 2026", "Due 14 mars 2026"], ["en-US"]);
    expect(asSecondOf("Due 14 mars 2026", { month: 4, reading })).toBe("Due 2 april 2026");
  });

  test("with nothing to settle it, is no date to edit: typing works as before", () => {
    const nothing = readingOf([], ["ja-JP"]);
    for (const text of ["14 April 2026", "14 Mai 2026", "14 mars 2026", "14 novembre 2026", "14 may 2026"]) {
      expect(dateIn(text, nothing)).toBeUndefined();
    }
  });

  test("written alike in every language that names it, needs nothing to settle it", () => {
    expect(asSecondOf("14 March 2026", { month: 4, reading: readingOf([]) })).toBe("2 April 2026");
  });
});

describe("a date's own marks", () => {
  test("a full stop after it, its language's own, keeps it in that language", () => {
    expect(asSecondOf("14. mart 2026.", { month: 9, reading: readingOf([], ["bs"]) })).toBe("2. septembar 2026.");
    expect(dateIn("14. mart 2026.", readingOf([], ["en-US"]))).toBeUndefined();
  });

  test("a part written in two digits is written in two", () => {
    const reading = readingOf(["31.12.2026"]);
    const date = dateIn("31.12.2026", reading);
    if (date === undefined) throw new Error("no date");
    expect(withDate("31.12.2026", date, { year: 2027, month: 1, day: 2 })).toBe("02.01.2027");
  });

  test("a two digit year is the one nearest this year that ends in it", () => {
    expect(dateIn("14.03.99", readingOf(["14.03.99"]))?.date.year).toBe(1999);
    expect(dateIn("14.03.26", readingOf(["14.03.26"]))?.date.year).toBe(2026);
  });

  test("its weekday is written for the new date, in its language, case and length", () => {
    const english = readingOf([], ["en-US"]);
    const long = "Saturday, 14 March 2026";
    const date = dateIn(long, english);
    if (date === undefined) throw new Error("no date");
    expect(long.slice(date.start, date.end)).toBe(long);
    expect(withDate(long, date, { year: 2026, month: 3, day: 16 })).toBe("Monday, 16 March 2026");
    const short = dateIn("SAT 14 MAR 2026", english);
    if (short === undefined) throw new Error("no date");
    expect(withDate("SAT 14 MAR 2026", short, { year: 2026, month: 3, day: 16 })).toBe("MON 16 MAR 2026");
    // A word before it that isn't its weekday stays outside it.
    expect(asSecondOf("Due 14 March 2026", { month: 3, reading: english })).toBe("Due 2 March 2026");
  });

  test("a weekday no language settles on leaves the date with no editor", () => {
    expect(dateIn("Lördag 14 mars 2026", readingOf([], ["ja-JP"]))).toBeUndefined();
  });
});

describe("a date's long forms", () => {
  test("words between its parts, as its language writes them", () => {
    const spanish = readingOf([], ["es-ES"]);
    expect(asSecondOf("14 de marzo de 2026", { month: 1, reading: spanish })).toBe("2 de enero de 2026");
    const portuguese = readingOf([], ["pt-BR"]);
    expect(asSecondOf("14 de março de 2026", { month: 1, reading: portuguese })).toBe("2 de janeiro de 2026");
  });

  test("a named month after its year", () => {
    expect(asSecondOf("2026. március 14.", { month: 4, reading: readingOf([]) })).toBe("2026. április 2.");
  });
});
