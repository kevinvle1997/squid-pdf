import { beforeAll, describe, expect, test } from "vitest";
import { learnDateLanguages } from "./dateNames";
import { type DateReading, type DateValue, dateReadingOf, datesIn, withDate } from "./dates";

// Every language a date is read in, learnt in the background as an editor would: the dates here need them.
beforeAll(() => learnDateLanguages());

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

/** `text`, its one date changed to `day` of March 2026, or undefined where no date is read. */
function asDayOf(text: string, { day, reading }: { day: number; reading: DateReading }) {
  const date = dateIn(text, reading);
  return date === undefined ? undefined : withDate(text, date, { year: 2026, month: 3, day });
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

describe("a month name, by the document's own months", () => {
  test("is read only in a language that names every month the document holds, never one sharing a single word", () => {
    const italian = readingOf(["2 Januar 2026", "3 Oktober 2026", "14 novembre 2026"], ["it-IT"]);
    expect(asSecondOf("14 novembre 2026", { month: 5, reading: italian })).toBeUndefined();
    const french = readingOf(["2 janvier 2026", "3 octobre 2026", "14 mar 2026"], ["es-ES"]);
    expect(asSecondOf("14 mar 2026", { month: 4, reading: french })).toBeUndefined();
    // Cebuano names "Enero" and "Sep", but not "marzo".
    const spanish = readingOf(["2 de enero de 2026", "3 de marzo de 2026", "14 Sep 2026"], ["es-ES"]);
    expect(asSecondOf("14 Sep 2026", { month: 10, reading: spanish })).toBeUndefined();
  });

  test("names it where the language would end it with a full stop, so the reader's languages decide among them", () => {
    // Dutch writes "feb", Swedish "feb.": both name every month here, and write March apart.
    const swedish = readingOf(["2 januari 2026", "14 feb 2026"], ["sv-SE"]);
    expect(asSecondOf("14 feb 2026", { month: 3, reading: swedish })).toBe("2 mars 2026");
    const dutchOrSwedish = ["2 januari 2026", "3 oktober 2026", "14 Dec 2026"];
    expect(asSecondOf("14 Dec 2026", { month: 1, reading: readingOf(dutchOrSwedish, ["en-US"]) })).toBeUndefined();
    expect(asSecondOf("14 Dec 2026", { month: 1, reading: readingOf(dutchOrSwedish, ["nl-NL"]) })).toBe("2 Jan 2026");
  });

  test("named in no one language CLDR covers fully, is no date to edit, though a constructed one names it all", () => {
    // Interlingue alone names both "Januar" and "may", and it's no document's language.
    const reading = readingOf(["2 Januar 2026", "14 may 2026"], ["es-ES"]);
    expect(asSecondOf("14 may 2026", { month: 3, reading })).toBeUndefined();
    expect(dateIn("14 marto 2026", readingOf([], ["eo"]))).toBeUndefined();
  });

  // "de" and "no" are months in Jola-Fonyi, "di" in a language CLDR covers only in part. The date is the first text.
  test.each([
    {
      texts: ["14 de marzo de 2026"],
      stray: "Página 1 de 12",
      reader: "es-ES",
      month: 1,
      comesTo: "2 de enero de 2026",
    },
    {
      texts: ["14 de marzo de 2026"],
      stray: "Resolución 12 de 2026",
      reader: "es-ES",
      month: 1,
      comesTo: "2 de enero de 2026",
    },
    {
      texts: ["14 de março de 2026"],
      stray: "Página 1 de 12",
      reader: "pt-BR",
      month: 1,
      comesTo: "2 de janeiro de 2026",
    },
    // Another date in Italian, as "marzo" alone is Spanish too.
    {
      texts: ["14 marzo 2026", "2 gennaio 2026"],
      stray: "Pagina 1 di 12",
      reader: "it-IT",
      month: 4,
      comesTo: "2 aprile 2026",
    },
    {
      texts: ["Published 14 March 2026"],
      stray: "Vol. 3, No. 12",
      reader: "en-US",
      month: 4,
      comesTo: "Published 2 April 2026",
    },
  ])(
    "is never a short word in a month's place, as in “$stray”, that only a thinly covered language names one",
    ({ texts, stray, reader, month, comesTo }) => {
      const [date = ""] = texts;
      expect(asSecondOf(date, { month, reading: readingOf([...texts, stray], [reader]) })).toBe(comesTo);
    },
  );

  test("is no month: a word a language names a month is read as one only in a date", () => {
    const french = readingOf(["Lun 16/03/2026"], ["fr-FR"]);
    expect(french.monthWords).toEqual([]);
    expect(asDayOf("Lun 16/03/2026", { day: 17, reading: french })).toBe("Mar 17/03/2026");
  });
});

describe("a date's own marks", () => {
  test("a full stop after it, its language's own, keeps it in that language", () => {
    expect(asSecondOf("14. mart 2026.", { month: 9, reading: readingOf([], ["bs"]) })).toBe("2. septembar 2026.");
    expect(dateIn("14. mart 2026.", readingOf([], ["en-US"]))).toBeUndefined();
  });

  test("a part written in one digit is written in one, and the day with it, which says nothing of its own", () => {
    const reading = readingOf(["14.3.2026"]);
    const date = dateIn("14.3.2026", reading);
    if (date === undefined) throw new Error("no date");
    expect(withDate("14.3.2026", date, { year: 2026, month: 3, day: 2 })).toBe("2.3.2026");
    expect(withDate("14.3.2026", date, { year: 2026, month: 11, day: 2 })).toBe("2.11.2026");
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
