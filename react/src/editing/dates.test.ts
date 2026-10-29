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

  test("a weekday beside a month in numbers is settled by itself, whatever months the document names", () => {
    const reading = readingOf(["Sat 14/03/2026", "Payé le 14 mars 2026"], ["en-US"]);
    expect(asDayOf("Sat 14/03/2026", { day: 16, reading })).toBe("Mon 16/03/2026");
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

describe("a range of days", () => {
  test("is no date to edit: an editor would rewrite only its last day", () => {
    const english = readingOf([], ["en-US"]);
    for (const text of ["14-15 March 2026", "14th-15th March 2026", "14 – 15 March 2026", "March 14-15, 2026"]) {
      expect(dateIn(text, english)).toBeUndefined();
    }
    expect(dateIn("14.-15. März 2026", readingOf([], ["de-DE"]))).toBeUndefined();
  });

  test("with a weekday before each day, is no date to edit, not even its last day alone", () => {
    const english = readingOf([], ["en-US"]);
    for (const text of ["Fri 13 – Sat 14 March 2026", "Friday 13 - Saturday 14 March 2026"]) {
      expect(dateIn(text, english)).toBeUndefined();
    }
  });

  test("joined by a slash, an ampersand, “and”, “to” or any dash, is no date to edit", () => {
    const english = readingOf([], ["en-US"]);
    for (const text of [
      "14 and 15 March 2026",
      "14 & 15 March 2026",
      "14 to 15 March 2026",
      "14/15 March 2026",
      "14\u201415 March 2026",
      "14\u201015 March 2026",
      "March 14/15, 2026",
      "March 14, 15, 2026",
    ]) {
      expect(dateIn(text, english)).toBeUndefined();
    }
  });

  test("after a word in lower case, is no date to edit", () => {
    expect(dateIn("The fair is held 13 – 15 March 2026", readingOf([], ["en-US"]))).toBeUndefined();
  });

  test("is told apart from a date after a number, or written with dashes throughout", () => {
    const english = readingOf([], ["en-US"]);
    expect(asSecondOf("Ref 123 - 14 March 2026", { month: 4, reading: english })).toBe("Ref 123 - 2 April 2026");
    expect(asSecondOf("14-Mar-2026", { month: 4, reading: english })).toBe("2-Apr-2026");
    expect(asSecondOf("Mar-14-26", { month: 4, reading: english })).toBe("Apr-2-26");
  });

  test("is told apart from a date after any other number: a version's, a page's, a time", () => {
    const english = readingOf([], ["en-US"]);
    for (const label of [
      "Version 3 – ",
      "Page 2 - ",
      "Rev. 4 - ",
      "Version 1.2 – ",
      "Rev 1.0 - ",
      "Draft v2 – ",
      "Page 1 of 3 – ",
      "Invoice #12 - ",
      "Section 4.2 - ",
      "Updated 10:30 – ",
      "Revision: 2 – ",
      "Pt. 3 – ",
    ]) {
      expect(asSecondOf(`${label}14 March 2026`, { month: 4, reading: english })).toBe(`${label}2 April 2026`);
    }
    const french = readingOf([], ["fr-FR"]);
    expect(asSecondOf("Facture n° 12 – 14 mars 2026", { month: 4, reading: french })).toBe(
      "Facture n° 12 – 2 avril 2026",
    );
  });

  test("with no year on its first date, is no date to edit, not one whose year is the next day", () => {
    expect(dateIn("14 March - 15 March 2026", readingOf([], ["en-US"]))).toBeUndefined();
  });
});

describe("a CJK date", () => {
  test("is written back in its own marks and spaces, its parts with no zero unless the page wrote one", () => {
    const reading = readingOf([]);
    expect(asSecondOf("2026年3月14日", { month: 4, reading })).toBe("2026年4月2日");
    expect(asSecondOf("2026년 3월 14일", { month: 4, reading })).toBe("2026년 4월 2일");
    expect(asSecondOf("2026年12月14日", { month: 1, reading })).toBe("2026年1月2日");
    expect(asSecondOf("2026年03月14日", { month: 11, reading })).toBe("2026年11月02日");
  });

  test("is read inside a sentence that runs on with no space after it", () => {
    const text = "支払期限は2026年3月14日までです";
    const date = dateIn(text, readingOf([]));
    if (date === undefined) throw new Error("no date");
    expect(text.slice(date.start, date.end)).toBe("2026年3月14日");
    expect(withDate(text, date, { year: 2026, month: 4, day: 2 })).toBe("支払期限は2026年4月2日までです");
  });

  test("its weekday after it is written for the new date, in its language and length", () => {
    const reading = readingOf([]);
    expect(asDayOf("2026年3月14日(土)", { day: 16, reading })).toBe("2026年3月16日(月)");
    expect(asDayOf("2026年3月14日（土曜日）", { day: 16, reading })).toBe("2026年3月16日（月曜日）");
    expect(asDayOf("2026年3月14日土曜日まで", { day: 16, reading })).toBe("2026年3月16日月曜日まで");
    expect(asDayOf("2026年3月14日 星期六", { day: 16, reading })).toBe("2026年3月16日 星期一");
    expect(asDayOf("2026년 3월 14일 토요일", { day: 16, reading })).toBe("2026년 3월 16일 월요일");
    expect(asDayOf("2026년 3월 14일 토", { day: 16, reading })).toBe("2026년 3월 16일 월");
  });

  test("a bracket after it that holds more than its weekday leaves it with no editor", () => {
    // Rewritten or left, what else the bracket says may no longer hold: 祝 marks a holiday.
    const reading = readingOf([], ["en-US"]);
    for (const text of ["2026年3月14日(土・祝)", "2026年3月14日(土曜日まで)", "2026年3月14日 (Sat, holiday)"]) {
      expect(dateIn(text, reading)).toBeUndefined();
    }
  });

  test("a weekday in brackets after it that no language settles on leaves it with no editor, though a word alone keeps it", () => {
    // The 19th is a Thursday, "to" in Finnish and in Nynorsk, which write the next day apart.
    const reading = readingOf([], ["en-US"]);
    expect(dateIn("2026年3月19日 (to)", reading)).toBeUndefined();
    expect(asDayOf("2026年3月19日 to Friday", { day: 20, reading })).toBe("2026年3月20日 to Friday");
  });

  test("its weekday in other letters is written for the new date, in brackets or as a word alone", () => {
    const reading = readingOf([], ["en-US"]);
    expect(asDayOf("2026年3月14日 (Sat)", { day: 16, reading })).toBe("2026年3月16日 (Mon)");
    expect(asDayOf("2026年3月14日(Sat)", { day: 16, reading })).toBe("2026年3月16日(Mon)");
    expect(asDayOf("2026年3月14日 Sat", { day: 16, reading })).toBe("2026年3月16日 Mon");
    expect(asDayOf("2026年3月14日 Saturday.", { day: 16, reading })).toBe("2026年3月16日 Monday.");
  });

  test("its weekday's full stop in a bracket is the weekday's own", () => {
    const reading = readingOf([], ["en-US"]);
    expect(asDayOf("2026年3月14日 (Sat.)", { day: 16, reading })).toBe("2026年3月16日 (Mon.)");
  });

  test("its weekday in square or lenticular brackets is in brackets too", () => {
    const reading = readingOf([], ["en-US"]);
    expect(asDayOf("2026年3月14日[土]", { day: 16, reading })).toBe("2026年3月16日[月]");
    expect(asDayOf("2026年3月14日［土］", { day: 16, reading })).toBe("2026年3月16日［月］");
    expect(asDayOf("2026年3月14日【土】", { day: 16, reading })).toBe("2026年3月16日【月】");
    expect(dateIn("2026年3月14日[土・祝]", reading)).toBeUndefined();
  });

  test("a time after it, joined by a dash, is no range's last day", () => {
    expect(asDayOf("2026年3月14日 - 10:00", { day: 16, reading: readingOf([]) })).toBe("2026年3月16日 - 10:00");
  });

  test("a range of days that runs on from it leaves it with no editor, though a range of two dates keeps both", () => {
    const reading = readingOf([]);
    for (const text of [
      "2026年3月14日～15日",
      "2026年3月14日(土)～15日(日)",
      "2026年3月14日-15日",
      "2026年3月14日から15日まで",
      "2026年3月14日〜3月20日",
      "2026년 3월 14일~15일",
    ]) {
      expect(dateIn(text, reading)).toBeUndefined();
    }
    expect(datesIn("2026年3月14日～2026年3月20日", reading)).toHaveLength(2);
  });

  test("its weekday is settled by itself, whatever months the document names", () => {
    const reading = readingOf(["期限 2026年3月14日(土)", "Due 14 March 2026"]);
    expect(asDayOf("期限 2026年3月14日(土)", { day: 16, reading })).toBe("期限 2026年3月16日(月)");
  });

  test("a word in other letters after it is no weekday, though it starts with the date's own", () => {
    // The 16th is a Monday, the 18th a Wednesday.
    const reading = readingOf([]);
    expect(asDayOf("2026年3月16日 Monthly report", { day: 17, reading })).toBe("2026年3月17日 Monthly report");
    expect(asDayOf("2026年3月18日 Wedding", { day: 19, reading })).toBe("2026年3月19日 Wedding");
  });

  test("a word in other letters after it doesn't take its editor, though it is or starts with a weekday elsewhere", () => {
    // The 14th is a Saturday ("Sa" in German), the 16th a Monday ("ma" in Dutch), the 19th a Thursday ("to" in
    // Finnish and Nynorsk, which write the next day apart).
    const reading = readingOf([]);
    expect(asDayOf("2026年3月14日 Sales report", { day: 16, reading })).toBe("2026年3月16日 Sales report");
    expect(asDayOf("2026年3月16日 manager", { day: 17, reading })).toBe("2026年3月17日 manager");
    const range = "2026年3月19日 to 2026年3月21日";
    expect(datesIn(range, readingOf([range])).map(({ start, end }) => range.slice(start, end))).toEqual([
      "2026年3月19日",
      "2026年3月21日",
    ]);
  });

  test("a letter after it that only starts a word is no weekday, though it names the date's own", () => {
    // The 15th is a Sunday, 日 in Japanese, and 日本 is Japan.
    expect(asDayOf("2026年3月15日日本にて", { day: 16, reading: readingOf([]) })).toBe("2026年3月16日日本にて");
    expect(asDayOf("2026년 3월 15일 일정", { day: 16, reading: readingOf([]) })).toBe("2026년 3월 16일 일정");
  });

  test("settles nothing, so a named date beside it keeps its editor", () => {
    const reading = readingOf(["2026年3月14日", "Due 14 March 2026"]);
    expect(reading.monthWords).toEqual(["march"]);
    expect(asSecondOf("Due 14 March 2026", { month: 4, reading })).toBe("Due 2 April 2026");
    expect(asSecondOf("2026年3月14日", { month: 4, reading })).toBe("2026年4月2日");
  });

  test("an era's year, a year in two digits, or a date the calendar lacks is no date to edit", () => {
    const reading = readingOf([]);
    for (const text of ["令和8年3月14日", "26年3月14日", "2026年3月", "2026年13月1日", "2026年2月30日"]) {
      expect(dateIn(text, reading)).toBeUndefined();
    }
  });
});

describe("a day written as an ordinal", () => {
  test("is written for the new day as its language writes it, in the page's case", () => {
    const reading = readingOf([]);
    expect(asSecondOf("14th March 2026", { month: 4, reading })).toBe("2nd April 2026");
    expect(asSecondOf("March 14th, 2026", { month: 4, reading })).toBe("April 2nd, 2026");
    expect(asSecondOf("21ST MARCH 2026", { month: 4, reading })).toBe("2ND APRIL 2026");
    for (const [day, written] of [
      [1, "1st"],
      [3, "3rd"],
      [11, "11th"],
      [12, "12th"],
      [13, "13th"],
      [22, "22nd"],
      [31, "31st"],
    ] as const) {
      expect(asDayOf("14th March 2026", { day, reading })).toBe(`${written} March 2026`);
    }
  });

  test("with its weekday before it", () => {
    expect(asDayOf("Saturday, 14th March 2026", { day: 16, reading: readingOf([]) })).toBe("Monday, 16th March 2026");
  });

  test("settles its date's language: “1er” is French's alone, which writes the other days plain", () => {
    const reading = readingOf([], ["en-US"]);
    const date = dateIn("1er mars 2026", reading);
    if (date === undefined) throw new Error("no date");
    expect(withDate("1er mars 2026", date, { year: 2026, month: 4, day: 2 })).toBe("2 avril 2026");
    expect(withDate("1er mars 2026", date, { year: 2026, month: 4, day: 1 })).toBe("1er avril 2026");
  });

  test("a document's dates with one count their month", () => {
    expect(readingOf(["Signed 2nd January 2026"]).monthWords).toEqual(["january"]);
    expect(readingOf(["Flat 4B May 2026"]).monthWords).toEqual([]);
  });

  test("not one its month's language writes, or none a language here writes, is no date to edit", () => {
    const reading = readingOf([], ["en-US", "fr-FR", "nl-NL"]);
    for (const text of ["1er March 2026", "14th mars 2026", "2st March 2026", "14e maart 2026", "4B May 2026"]) {
      expect(dateIn(text, reading)).toBeUndefined();
    }
  });
});
