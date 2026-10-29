// Ranges of days around a named date, read so that no editor rewrites one end of one. Only an unmistakable range
// counts: a plain date losing its editor is worse than a range missed.
import { isNameOf, namesOf } from "./dateNames";
import { type CalendarDay, type DateFormat, weekdayOf } from "./dates";

/** A named date as its pattern read it: its parts, what's between them, the text around it, and its weekday. */
export interface DateInText {
  readonly order: DateFormat["order"];
  readonly parts: readonly string[];
  readonly between: readonly [string, string];
  readonly before: string; // up to its day, or its weekday where it has one
  readonly after: string;
  readonly hasWeekday: boolean;
  readonly language: string; // the language its month is read in
}

// What joins two days of a range: a hyphen, any dash or a slash.
const JOINER = String.raw`[-–\u2014‐‑/]`;
// A first day not run on from a letter, a digit or a mark, as "2" in "1.2", "12" in "#12" and "1" in "£1" are.
const BARE = String.raw`(?<![\p{L}\p{N}\p{Sc}.,:#°º/\-])`;
// A range's first day, perhaps with an ordinal's suffix or a full stop, its number captured.
const FIRST_DAY = String.raw`(\d{1,2})(?:\p{L}{1,2}|\.)?`;
// How a range's first day can end the text before its last, each unmistakable: joined tight, joined by a word, or
// all the text there is before it.
const FIRST_DAY_ENDINGS = [
  new RegExp(`${BARE}${FIRST_DAY}${JOINER}$`, "u"),
  new RegExp(String.raw`${BARE}${FIRST_DAY}(?:\s+(?:and|to)\s+|\s*&\s*)$`, "u"),
  new RegExp(String.raw`^\s*${FIRST_DAY}\s*${JOINER}\s*$`, "u"),
];
// A range's first day after its own weekday, as its last has one too: "Fri 13 – " before "Sat 14 March".
const FIRST_DAY_WITH_WEEKDAY = new RegExp(String.raw`(\p{L}+)\.?,?\s+${FIRST_DAY}\s*${JOINER}\s*$`, "u");
// What may come before a range's first date: nothing, or a word, perhaps its weekday, captured.
const LEAD = String.raw`(?:^\s*|(?<![\p{L}\p{N}])(\p{L}+)\.?,?\s+)`;
// A range's first date, its day and its month's name either way round with no year, and which group holds each:
// "14 March – " before "15 March 2026", "Fri, Mar 13 – " before "Sat, Mar 14, 2026".
const FIRST_DATES = [
  { pattern: new RegExp(String.raw`${LEAD}${FIRST_DAY}\s+(\p{L}+)\.?\s*${JOINER}\s*$`, "u"), day: 2, month: 3 },
  { pattern: new RegExp(String.raw`${LEAD}(\p{L}+)\.?\s+${FIRST_DAY},?\s*${JOINER}\s*$`, "u"), day: 3, month: 2 },
];
// A four digit year after a date: "15, 2026" after "March 14", which makes 15 a range's last day.
const YEAR_AFTER = /^,\s*\d{4}(?![\p{L}\p{N}])(?![./-]\p{N})/u;
// The rest of a date after a day, its month's name and its year, perhaps with a full stop or a short word its
// language writes between: " March 2026", ". März 2026", " de marzo de 2026".
const REST_OF_DATE =
  /^\.?\s+(?:\p{Ll}{1,3}\s+)?(\p{L}+)\.?(?:\s+\p{Ll}{1,3})?,?\s+(?:\d{4}|\d{2})(?![\p{L}\p{N}])(?![./-]\p{N})/u;

/** Whether a named date is one end of a range of days, so that an editor would rewrite only that end. */
export function inDayRange(date: CalendarDay, inText: DateInText): boolean {
  const [first] = inText.order;
  if (first === "month") return endsInLastDay(inText) || followsFirstDate(date, inText);
  if (first === "day") return endsInFirstDay(date, inText);
  // Year first, a range runs on from its day, which the date's pattern refuses: "2026. március 14-15.".
  return false;
}

/**
 * Whether a month-first date's two digit year is a range's last day: joined to its day tight, or by more than a
 * space with a year after a comma. Else it reads as a year, "Mar 14 26 1430" as "Mar 14, 26" does.
 */
function endsInLastDay({ parts, between, after }: DateInText): boolean {
  if (parts[2]?.length !== 2) return false;
  const [monthToDay, dayToYear] = between;
  // Joined so between its month and day too, "Mar-14-26", it's a date.
  const tight = /^[-/]$/.test(dayToYear) && monthToDay !== dayToYear;
  const yearAfter = !/^\s+$/.test(dayToYear) && YEAR_AFTER.test(after);
  return tight || yearAfter;
}

/** Whether the text before a day-first date ends in a range's first day, unmistakably joined to the date's. */
function endsInFirstDay(date: CalendarDay, inText: DateInText): boolean {
  const { before, hasWeekday } = inText;
  // Joined to the date's own day: across its weekday, as in "Steps 1 to Saturday, 14 March", no two days are.
  const endings = hasWeekday ? [] : FIRST_DAY_ENDINGS;
  const joinedDay = endings.map((ending) => ending.exec(before)?.[1]).find((day) => day !== undefined);
  if (joinedDay !== undefined) return Number(joinedDay) < date.day;
  if (followsFirstDate(date, inText)) return true;
  // Spaced after a word, it's a range only where each day has its weekday.
  if (!hasWeekday) return false;
  const [, weekday = "", day = ""] = FIRST_DAY_WITH_WEEKDAY.exec(before) ?? [];
  const firstDay = { ...date, day: Number(day) };
  return firstDay.day < date.day && isWeekdayOf(weekday, firstDay);
}

/**
 * Whether the text before `date` ends in a range's first date with no year: its month the date's or an earlier one
 * in the date's language, at the text's start or after its own weekday. After any other word, or later in the year,
 * it may be a date of its own: "Set 2 – 14 March", "May 2 - 14 March".
 */
function followsFirstDate(date: CalendarDay, { before, language }: DateInText): boolean {
  const first = firstDateIn(before, { year: date.year, language });
  if (first === undefined) return false;
  const earlier = first.day.month < date.month || (first.day.month === date.month && first.day.day < date.day);
  const leads = first.lead === "" || isWeekdayOf(first.lead, first.day);
  return earlier && leads;
}

/** The range's first date ending `before`, its month named in `language`, and the word before it. */
function firstDateIn(
  before: string,
  { year, language }: { year: number; language: string },
): { day: CalendarDay; lead: string } | undefined {
  for (const { pattern, day, month } of FIRST_DATES) {
    const found = pattern.exec(before);
    const index = monthIndexIn(found?.[month] ?? "", language);
    if (found !== null && index !== undefined) {
      return { day: { year, month: index, day: Number(found[day]) }, lead: found[1] ?? "" };
    }
  }
  return undefined;
}

/** Which month `word` names in `language`, or in its other forms, from 1. */
function monthIndexIn(word: string, language: string): number | undefined {
  const base = new Intl.Locale(language).language;
  return namesOf(word, "month").find((name) => new Intl.Locale(name.language).language === base)?.index;
}

/**
 * Whether a date's two digit year after a hyphen is another date's day, a whole date's month and year after it: "15"
 * in "14 March - 15 March 2026", "14" in "May 2 - 14 March 2026". That other date is the one to read.
 */
export function yearIsAnotherDatesDay({
  order,
  parts,
  between,
  after,
}: Pick<DateInText, "order" | "parts" | "between" | "after">): boolean {
  const dashed = order[2] === "year" && parts[2]?.length === 2 && /^\s*-\s*$/.test(between[1]);
  return dashed && isNameOf(REST_OF_DATE.exec(after)?.[1] ?? "", "month");
}

/** Whether some language names `word` the day of the week `day` falls on. */
function isWeekdayOf(word: string, day: CalendarDay): boolean {
  const index = weekdayOf(day);
  return namesOf(word, "weekday").some((name) => name.index === index);
}
