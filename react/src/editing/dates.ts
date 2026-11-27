import {
  caseOf,
  type DateName,
  isNameOf,
  type NameKind,
  type NameWord,
  nameFor,
  namesOf,
  wordsBetweenIn,
} from "./dateNames";
import { type DateInText, inDayRange, yearIsAnotherDatesDay } from "./dayRanges";
import type { Extent } from "./formattedValues";
import {
  type DayOrdinal,
  type OrdinalWord,
  ordinalWordOf,
  someLanguageWrites,
  suffixFor,
  writesOrdinal,
} from "./ordinals";

export interface CalendarDay {
  readonly year: number;
  readonly month: number;
  readonly day: number;
}

type DatePart = "day" | "month" | "year";

export interface DateFormat {
  readonly order: readonly [DatePart, DatePart, DatePart];
  readonly between: readonly [string, string];
  readonly closing: string;
  readonly digits: { readonly day: 1 | 2; readonly month: 1 | 2; readonly year: 2 | 4 };
  readonly month: NameWord | null;
  readonly ordinal: OrdinalWord | null;
  readonly weekday: WeekdayFormat | null;
}

interface WeekdayFormat {
  readonly word: NameWord;
  readonly side: "before" | "after";
  readonly around: readonly [string, string];
}

export interface DateValue extends Extent {
  readonly kind: "date";
  readonly date: CalendarDay;
  readonly format: DateFormat;
}

export type DateOrder = "day-first" | "month-first";

export interface DateReading {
  readonly order: DateOrder | undefined;
  readonly monthWords: readonly string[];
  readonly readerLanguages: readonly string[];
}

const BETWEEN = String.raw`(?:[\s\u00a0\u202f.,/\-]{1,3}|[\s\u00a0\u202f]+\p{Ll}{1,3}[\s\u00a0\u202f]+)`;
const NAME = String.raw`\p{L}+\.?`;
const DAY = String.raw`\d{1,2}\p{L}{0,2}`;
const YEAR = String.raw`\d{4}|\d{2}`;
const BEFORE = String.raw`(?:(\p{L}+)(\.?,?[\s\u00a0\u202f]+))?`;
const ALONE_BEFORE = String.raw`(?<![\p{L}\p{N}])`;
const ALONE_AFTER = String.raw`(?![\p{L}\p{N}])`;
const LONGER_DATE_REST = /^\s*(\p{L}+)\.?,?\s+(?:\d{4}|\d{2})(?!\p{N})/u;
const NO_MORE_NUMBERS = String.raw`(?![./-]\p{N})`;

const NAMED: readonly { readonly order: DateFormat["order"]; readonly pattern: RegExp }[] = [
  { order: ["day", "month", "year"], pattern: named(DAY, NAME, YEAR) },
  { order: ["month", "day", "year"], pattern: named(NAME, DAY, YEAR) },
  { order: ["year", "month", "day"], pattern: named(String.raw`\d{4}`, NAME, DAY) },
];

function named(...parts: [string, string, string]): RegExp {
  const [first, second, third] = parts;
  return new RegExp(
    `${ALONE_BEFORE}${BEFORE}(${first})(${BETWEEN})(${second})(${BETWEEN})(${third})${ALONE_AFTER}${NO_MORE_NUMBERS}`,
    "gu",
  );
}

const NUMERIC = new RegExp(
  `(?<![\\p{L}\\p{N}])(?<!\\p{N}[./-])${BEFORE}(\\d{1,4})([./-])(\\d{1,2})\\4(\\d{1,4})(?![\\p{L}\\p{N}])(?![./-]\\p{N})`,
  "gu",
);

// Its year in four digits: an era's year, 令和8年, is no year of the Gregorian calendar.
const CJK = (
  [
    ["年", "月", "日"],
    ["년", "월", "일"],
  ] as const
).map(
  ([year, month, day]) =>
    new RegExp(String.raw`(?<!\p{N})(\d{4})(\s*${year}\s*)(\d{1,2})(\s*${month}\s*)(\d{1,2})(\s*${day})`, "gu"),
);
const AFTER_CJK = /^(\s*)([(（[［【]?)(\p{L}+)(\.?)([)）\]］】]?)/u;
const CJK_LETTER = /^[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}\p{sc=Hangul}]/u;
// A range's last day has its own mark, 日 or 일, as every CJK range writes it: a number without one is a time or a count.
const CJK_RANGE_AFTER = /^\s*(?:[～~〜\-–‐\u2014]|から)\s*(?:\d{1,2}\s*[月월]\s*)?\d{1,2}\s*[日일]/u;
const DATE_AFTER_WORD = /^\s*\d{4}\s*[年년]\s*\d{1,2}\s*[月월]\s*\d{1,2}\s*[日일]/u;

// A two digit year is the year nearest this one that ends in it: in 2026, 99 is 1999 and 50 is 2050.
const CENTURY = 100;

export function dateReadingOf(
  texts: Iterable<string>,
  { readerLanguages }: { readerLanguages: readonly string[] },
): DateReading {
  const orders = new Set<DateOrder>();
  const monthWords = new Set<string>();
  for (const text of texts) {
    const numericOrders = readEach(text, {
      pattern: NUMERIC,
      read: ([, , , first = "", , second = "", last = ""]) =>
        first.length === 4 || !isYear(last) ? undefined : onlyOrderOf(Number(first), Number(second)),
    });
    for (const order of numericOrders) orders.add(order);
    for (const { order, pattern } of NAMED) {
      const words = readEach(text, {
        pattern,
        read: (match) => monthWordIn(match, order),
      });
      for (const word of words) monthWords.add(word);
    }
  }
  const [only] = orders;
  return { order: orders.size === 1 ? only : undefined, monthWords: [...monthWords], readerLanguages };
}

function monthWordIn(match: RegExpMatchArray, order: DateFormat["order"]): string | undefined {
  const { parts, between } = partsOf(match);
  if (yearIsAnotherDatesDay({ order, parts, between, after: textAfter(match) })) return undefined;
  const word = parts[order.indexOf("month")]?.replace(/\.$/, "") ?? "";
  if (startsLongerDate(match, word)) return undefined;
  const { ordinal } = writtenDayOf(parts[order.indexOf("day")] ?? "");
  const named = isNameOf(word, "month") && (ordinal === undefined || someLanguageWrites(ordinal));
  return named ? word.toLowerCase() : undefined;
}

function onlyOrderOf(first: number, second: number): DateOrder | undefined {
  if (first > 12 && second <= 12) return "day-first";
  if (second > 12 && first <= 12) return "month-first";
  return undefined;
}

function isYear(digits: string): boolean {
  return digits.length === 2 || digits.length === 4;
}

export function datesIn(text: string, reading: DateReading): DateValue[] {
  const named = NAMED.flatMap(({ order, pattern }) =>
    readEach(text, { pattern, read: (match) => namedDateOf(match, { order, reading }) }),
  );
  const cjk = CJK.flatMap((pattern) => readEach(text, { pattern, read: (match) => cjkDateOf(match, reading) }));
  return [...named, ...readEach(text, { pattern: NUMERIC, read: (match) => numericDateOf(match, reading) }), ...cjk];
}

const UNSETTLED = Symbol("unsettled");

/** A match `read` makes nothing of is tried again a letter on: "40 on 14" looks like a date, and "14 March 2026" follows. */
function readEach<T>(
  text: string,
  { pattern, read }: { pattern: RegExp; read: (match: RegExpExecArray) => T | undefined | typeof UNSETTLED },
): T[] {
  const found: T[] = [];
  const searching = new RegExp(pattern.source, pattern.flags);
  for (let match = searching.exec(text); match !== null; match = searching.exec(text)) {
    const value = read(match);
    if (value === undefined) searching.lastIndex = match.index + 1;
    else if (value !== UNSETTLED) found.push(value);
  }
  return found;
}

function partsOf(match: RegExpMatchArray) {
  const [whole, before = "", beforeSpace = "", first = "", between1 = "", second = "", between2 = "", third = ""] =
    match;
  return { whole, before, beforeSpace, parts: [first, second, third] as const, between: [between1, between2] as const };
}

function namedDateOf(
  match: RegExpMatchArray,
  { order, reading }: { order: DateFormat["order"]; reading: DateReading },
): DateValue | undefined | typeof UNSETTLED {
  const { whole, before, beforeSpace, parts, between } = partsOf(match);
  if (yearIsAnotherDatesDay({ order, parts, between, after: textAfter(match) })) return undefined;
  const at = order.indexOf("month");
  let monthWord = parts[at] ?? "";
  let betweenParts: [string, string] = [between[0], between[1]];
  if (!isNameOf(monthWord, "month") && monthWord.endsWith(".")) {
    monthWord = monthWord.slice(0, -1);
    betweenParts = at === 0 ? [`.${betweenParts[0]}`, betweenParts[1]] : [betweenParts[0], `.${betweenParts[1]}`];
  }
  if (startsLongerDate(match, monthWord)) return undefined;
  const day = writtenDayOf(parts[order.indexOf("day")] ?? "");
  const monthOnlyWords = { month: monthWord, between: betweenParts, ordinal: day.ordinal };
  const monthOnly = settledNames(monthOnlyWords, reading);
  if (monthOnly?.month === undefined) return undefined;
  const year = parts[order.indexOf("year")] ?? "";
  const date = dayOf({ year, month: monthOnly.month.index, day: Number(day.digits) });
  if (date === undefined) return undefined;
  const weekday = asWeekday(before, date);
  // A range of days: an editor would rewrite only one end of it.
  if (inDayRange(date, dateInTextOf(match, { order, weekday, language: monthOnly.language }))) return UNSETTLED;
  const settled = weekday === undefined ? monthOnly : settledNames({ ...monthOnlyWords, weekday }, reading);
  if (settled?.month === undefined) return UNSETTLED;
  const format: DateFormat = {
    order,
    between: betweenParts,
    closing: "",
    digits: { day: hasZero(day.digits) ? 2 : 1, month: 1, year: digitsOf(year) },
    month: wordOf(settled.month, monthWord),
    ordinal: day.ordinal === undefined ? null : ordinalWordOf(settled.language, day.ordinal),
    weekday: settled.weekday === undefined ? null : leadingWeekdayFormat(settled.weekday, { before, beforeSpace }),
  };
  return located({ date, format }, { match, whole, before: weekday === undefined ? before + beforeSpace : "" });
}

function startsLongerDate(match: RegExpMatchArray, monthWord: string): boolean {
  const restIsMonth = isNameOf(LONGER_DATE_REST.exec(textAfter(match))?.[1] ?? "", "month");
  return isNameOf(monthWord, "weekday") && restIsMonth;
}

function dateInTextOf(
  match: RegExpMatchArray,
  { order, weekday, language }: { order: DateFormat["order"]; weekday: WeekdayWord | undefined; language: string },
): DateInText {
  const { before, beforeSpace, parts, between } = partsOf(match);
  const at = match.index ?? 0;
  const dateAt = at + (weekday === undefined ? before.length + beforeSpace.length : 0);
  const hasWeekday = weekday !== undefined;
  const textBefore = (match.input ?? "").slice(0, dateAt);
  return { order, parts, between, before: textBefore, after: textAfter(match), hasWeekday, language };
}

function textAfter(match: RegExpMatchArray): string {
  return (match.input ?? "").slice((match.index ?? 0) + match[0].length);
}

function numericDateOf(match: RegExpMatchArray, reading: DateReading): DateValue | undefined | typeof UNSETTLED {
  const [whole, before = "", beforeSpace = "", first = "", mark = "", second = "", last = ""] = match;
  const yearFirst = first.length === 4 && last.length <= 2;
  if (!yearFirst && (first.length > 2 || !isYear(last))) return undefined;
  const order = yearFirst ? "year-first" : (onlyOrderOf(Number(first), Number(second)) ?? reading.order);
  // Read either way and nothing settles which: an editor that guessed would write a wrong date that looks right.
  if (order === undefined) return undefined;
  const parts = numericPartsOf(order, { first, second, last });
  const date = dayOf({ year: parts.year, month: Number(parts.month), day: Number(parts.day) });
  if (date === undefined) return undefined;
  const weekday = asWeekday(before, date);
  const named = weekday === undefined ? undefined : settledWeekday(weekday, reading);
  if (named === UNSETTLED) return UNSETTLED;
  const format: DateFormat = {
    order: parts.order,
    between: [mark, mark],
    closing: "",
    digits: { ...numericDigitsOf(parts, { unshown: 2 }), year: digitsOf(parts.year) },
    month: null,
    ordinal: null,
    weekday: named === undefined ? null : leadingWeekdayFormat(named, { before, beforeSpace }),
  };
  return located({ date, format }, { match, whole, before: weekday === undefined ? before + beforeSpace : "" });
}

function cjkDateOf(match: RegExpExecArray, reading: DateReading): DateValue | undefined | typeof UNSETTLED {
  const [whole, year = "", afterYear = "", month = "", afterMonth = "", day = "", closing = ""] = match;
  const date = dayOf({ year, month: Number(month), day: Number(day) });
  if (date === undefined) return undefined;
  const format: DateFormat = {
    order: ["year", "month", "day"],
    between: [afterYear, afterMonth],
    closing,
    digits: { ...numericDigitsOf({ day, month }, { unshown: 1 }), year: 4 },
    month: null,
    ordinal: null,
    weekday: null,
  };
  const dated = settleTrailingWeekday(located({ date, format }, { match, whole, before: "" }), {
    text: match.input,
    reading,
  });
  if (dated === UNSETTLED) return UNSETTLED;
  // A range of days, "14日～15日": an editor would rewrite only its first.
  if (CJK_RANGE_AFTER.test(match.input.slice(dated.end))) return UNSETTLED;
  return dated;
}

function settleTrailingWeekday(
  value: DateValue,
  { text, reading }: { text: string; reading: DateReading },
): DateValue | typeof UNSETTLED {
  const weekday = trailingWeekdayIn(text.slice(value.end), value.date);
  if (weekday === undefined) return value;
  if (weekday === UNSETTLED) return UNSETTLED;
  const named = settledWeekday(weekday, reading);
  if (named === UNSETTLED) return UNSETTLED;
  const format = { ...value.format, weekday: trailingWeekdayFormat(named, weekday) };
  return { ...value, format, end: value.end + weekday.length };
}

function writtenDayOf(written: string): { digits: string; ordinal: DayOrdinal | undefined } {
  const digits = /^\d+/.exec(written)?.[0] ?? "";
  const suffix = written.slice(digits.length);
  return { digits, ordinal: suffix === "" ? undefined : { day: Number(digits), suffix } };
}

function settledWeekday(weekday: WeekdayWord, reading: DateReading): DateName | typeof UNSETTLED {
  return settledNames({ weekday, between: [] }, reading)?.weekday ?? UNSETTLED;
}

function leadingWeekdayFormat(
  name: DateName,
  { before, beforeSpace }: { before: string; beforeSpace: string },
): WeekdayFormat {
  const ownStop = name.name.endsWith(".") && beforeSpace.startsWith(".");
  const written = ownStop ? `${before}.` : before;
  return { word: wordOf(name, written), side: "before", around: ["", ownStop ? beforeSpace.slice(1) : beforeSpace] };
}

function trailingWeekdayFormat(name: DateName, weekday: TrailingWeekday): WeekdayFormat {
  return { word: wordOf(name, weekday.word), side: "after", around: weekday.around };
}

function located(
  { date, format }: Pick<DateValue, "date" | "format">,
  { match, whole, before }: { match: RegExpMatchArray; whole: string; before: string },
): DateValue {
  const start = (match.index ?? 0) + before.length;
  return { kind: "date", date, format, start, end: (match.index ?? 0) + whole.length };
}

function numericPartsOf(
  order: DateOrder | "year-first",
  { first, second, last }: { first: string; second: string; last: string },
): { day: string; month: string; year: string; order: DateFormat["order"] } {
  switch (order) {
    case "year-first":
      return { year: first, month: second, day: last, order: ["year", "month", "day"] };
    case "day-first":
      return { day: first, month: second, year: last, order: ["day", "month", "year"] };
    case "month-first":
      return { month: first, day: second, year: last, order: ["month", "day", "year"] };
    default:
      return order satisfies never;
  }
}

function dayOf({ year, month, day }: { year: string; month: number; day: number }): CalendarDay | undefined {
  const fullYear = year.length === 2 ? nearestYearEndingIn(Number(year)) : Number(year);
  if (month < 1 || month > 12 || day < 1 || day > daysIn(fullYear, month)) return undefined;
  return { year: fullYear, month, day };
}

function nearestYearEndingIn(twoDigits: number, thisYear = new Date().getUTCFullYear()): number {
  const sameCentury = thisYear - (thisYear % CENTURY) + twoDigits;
  const candidates = [sameCentury - CENTURY, sameCentury, sameCentury + CENTURY];
  return candidates.reduce((best, year) => (Math.abs(year - thisYear) < Math.abs(best - thisYear) ? year : best));
}

export function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function hasZero(digits: string): boolean {
  return digits.length === 2 && digits.startsWith("0");
}

function numericDigitsOf(
  { day, month }: { day: string; month: string },
  { unshown }: { unshown: 1 | 2 },
): { day: 1 | 2; month: 1 | 2 } {
  const dayShows = digitsShownBy(day);
  const monthShows = digitsShownBy(month);
  return { day: dayShows ?? monthShows ?? unshown, month: monthShows ?? dayShows ?? unshown };
}

function digitsShownBy(digits: string): 1 | 2 | undefined {
  if (digits.length === 1) return 1;
  if (digits.startsWith("0")) return 2;
  return undefined;
}

function digitsOf(year: string): 2 | 4 {
  return year.length === 2 ? 2 : 4;
}

function wordOf(name: DateName, written: string): NameWord {
  const stopLeftOff = name.name.endsWith(".") && !written.endsWith(".");
  const own = stopLeftOff ? name.name.slice(0, -1) : name.name;
  return { language: name.language, width: name.width, letterCase: caseOf(written, own), stopLeftOff };
}

export function withDate(text: string, value: DateValue, date: CalendarDay): string {
  return text.slice(0, value.start) + writtenDate(date, value.format) + text.slice(value.end);
}

function writtenDate(date: CalendarDay, format: DateFormat): string {
  const { digits } = format;
  const year = digits.year === 2 ? String(date.year % CENTURY).padStart(2, "0") : String(date.year);
  const month =
    format.month === null ? String(date.month).padStart(digits.month, "0") : nameFor("month", date.month, format.month);
  const ordinal = format.ordinal === null ? "" : suffixFor(date.day, format.ordinal);
  const parts = { day: String(date.day).padStart(digits.day, "0") + ordinal, month, year };
  const [first, second, third] = format.order;
  const written = parts[first] + format.between[0] + parts[second] + format.between[1] + parts[third] + format.closing;
  if (format.weekday === null) return written;
  const { word, side, around } = format.weekday;
  const weekday = around[0] + nameFor("weekday", weekdayOf(date), word) + around[1];
  return side === "before" ? weekday + written : written + weekday;
}

/** Why: a word beside a date that names another day isn't its weekday, but a word in some other language. */
function asWeekday(word: string, date: CalendarDay): WeekdayWord | undefined {
  const index = weekdayOf(date);
  return namesOf(word, "weekday").some((name) => name.index === index) ? { word, index } : undefined;
}

function trailingWeekdayIn(rest: string, date: CalendarDay): TrailingWeekday | undefined | typeof UNSETTLED {
  const [whole = "", space = "", open = "", letters = "", stop = "", close = ""] = AFTER_CJK.exec(rest) ?? [];
  const weekday = CJK_LETTER.test(letters) ? longestWeekdayIn(letters, date) : asWeekday(letters, date);
  if (weekday === undefined) return undefined;
  const bracketed = open !== "";
  if (!bracketed && DATE_AFTER_WORD.test(rest.slice(whole.length))) return undefined;
  const fillsBracket = weekday.word === letters && close !== "";
  if (bracketed && !fillsBracket) return UNSETTLED;
  const around = [space + open, bracketed ? stop + close : ""] as const;
  return { ...weekday, around, length: around[0].length + weekday.word.length + around[1].length };
}

/** One letter that runs on into more starts a word, as 日 starts 日本, so it counts only alone. */
function longestWeekdayIn(letters: string, date: CalendarDay): WeekdayWord | undefined {
  for (let length = letters.length; length > 0; length--) {
    const wholeOrLong = length === letters.length || length > 1;
    const weekday = wholeOrLong ? asWeekday(letters.slice(0, length), date) : undefined;
    if (weekday !== undefined) return weekday;
  }
  return undefined;
}

export function weekdayOf({ year, month, day }: CalendarDay): number {
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

interface Named {
  readonly language: string;
  readonly month?: DateName;
  readonly weekday?: DateName;
}

interface WeekdayWord {
  readonly word: string;
  readonly index: number;
}

interface TrailingWeekday extends WeekdayWord {
  readonly around: readonly [string, string];
  readonly length: number;
}

interface DateWords {
  readonly month?: string;
  readonly weekday?: WeekdayWord;
  readonly between: readonly string[];
  readonly ordinal?: DayOrdinal;
}

/** Undefined where no one language settles them, so no editor guesses. */
function settledNames(words: DateWords, reading: DateReading): Named | undefined {
  const naming = languagesNaming(words);
  const ofDocument = naming.filter((named) => reading.monthWords.every((word) => namesMonth(named, word)));
  const weekdayAlone = ofDocument.length === 0 && words.month === undefined;
  const counting = weekdayAlone ? naming : ofDocument;
  if (counting.length === 0) return undefined;
  if (writeAlike(counting, words)) return counting[0];
  return readersOf(counting, { words, reading });
}

function languagesNaming(words: DateWords): Named[] {
  const kinds = kindsIn(words);
  const byLanguage = new Map<string, Partial<Record<NameKind, DateName[]>>>();
  for (const kind of kinds) {
    for (const name of namesIn(words, kind)) {
      const found = byLanguage.get(name.language) ?? {};
      found[kind] = [...(found[kind] ?? []), name];
      byLanguage.set(name.language, found);
    }
  }
  const all = [...byLanguage].flatMap(([language, found]) => {
    if (!kinds.every((kind) => found[kind] !== undefined)) return [];
    const pick = (kind: NameKind) => bestOf(found[kind] ?? [], wordIn(words, kind));
    return [{ language, month: pick("month"), weekday: pick("weekday") }];
  });
  return all.filter((named) => writesBetween(named, words.between) && writesDay(named, words.ordinal));
}

function writesDay(named: Named, ordinal: DayOrdinal | undefined): boolean {
  return ordinal === undefined || writesOrdinal(named.language, ordinal);
}

function kindsIn(words: DateWords): NameKind[] {
  return (["month", "weekday"] as const).filter((kind) => words[kind] !== undefined);
}

function wordIn(words: DateWords, kind: NameKind): string {
  return kind === "month" ? (words.month ?? "") : (words.weekday?.word ?? "");
}

function namesIn(words: DateWords, kind: NameKind): readonly DateName[] {
  const names = namesOf(wordIn(words, kind), kind);
  if (kind === "month") return names;
  return names.filter((name) => name.index === words.weekday?.index);
}

function bestOf(names: readonly DateName[], written: string): DateName | undefined {
  return names.find((name) => name.name === written) ?? names[0];
}

function writesBetween(named: Named, between: readonly string[]): boolean {
  const words = between.flatMap((part) => part.toLowerCase().match(/\p{L}+/gu) ?? []);
  if (words.length === 0) return true;
  const own = wordsBetweenIn(named.language, named.month?.width ?? "long");
  return words.every((word) => own.includes(word));
}

function writeAlike(languages: readonly Named[], words: DateWords): boolean {
  return kindsIn(words).every((kind) => {
    const count = kind === "month" ? 12 : 7;
    const first = kind === "month" ? 1 : 0;
    return Array.from({ length: count }, (_, at) => at + first).every((index) => {
      const written = new Set(languages.map((named) => writtenIn(named, { kind, index, words })));
      return written.size === 1;
    });
  });
}

function writtenIn(named: Named, { kind, index, words }: { kind: NameKind; index: number; words: DateWords }): string {
  const name = named[kind];
  if (name === undefined) return "";
  return nameFor(kind, index, wordOf(name, wordIn(words, kind)));
}

function namesMonth(named: Named, word: string): boolean {
  return namesOf(word, "month").some((name) => name.language === named.language);
}

function readersOf(
  languages: readonly Named[],
  { words, reading }: { words: DateWords; reading: DateReading },
): Named | undefined {
  const baseOf = (tag: string) => new Intl.Locale(tag).language;
  for (const tag of reading.readerLanguages) {
    const same = languages.filter((named) => baseOf(named.language) === baseOf(tag));
    if (same.length === 0) continue;
    const exactly = same.filter((named) => named.language.toLowerCase() === tag.toLowerCase());
    const plain = same.filter((named) => named.language === baseOf(tag));
    const settled = [exactly, plain, same].find((set) => set.length > 0 && writeAlike(set, words));
    return settled?.[0];
  }
  return undefined;
}
