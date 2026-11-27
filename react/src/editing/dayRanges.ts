// Only an unmistakable range counts: a plain date losing its editor is worse than a range missed.
import { isNameOf, namesOf } from "./dateNames";
import { type CalendarDay, type DateFormat, weekdayOf } from "./dates";

export interface DateInText {
  readonly order: DateFormat["order"];
  readonly parts: readonly string[];
  readonly between: readonly [string, string];
  readonly before: string;
  readonly after: string;
  readonly hasWeekday: boolean;
  readonly language: string;
}

const JOINER = String.raw`[-–\u2014‐‑/]`;
const BARE = String.raw`(?<![\p{L}\p{N}\p{Sc}.,:#°º/\-])`;
const FIRST_DAY = String.raw`(\d{1,2})(?:\p{L}{1,2}|\.)?`;
const FIRST_DAY_ENDINGS = [
  new RegExp(`${BARE}${FIRST_DAY}${JOINER}$`, "u"),
  new RegExp(String.raw`${BARE}${FIRST_DAY}(?:\s+(?:and|to)\s+|\s*&\s*)$`, "u"),
  new RegExp(String.raw`^\s*${FIRST_DAY}\s*${JOINER}\s*$`, "u"),
];
const FIRST_DAY_WITH_WEEKDAY = new RegExp(String.raw`(\p{L}+)\.?,?\s+${FIRST_DAY}\s*${JOINER}\s*$`, "u");
const LEAD = String.raw`(?:^\s*|(?<![\p{L}\p{N}])(\p{L}+)\.?,?\s+)`;
const FIRST_DATES = [
  { pattern: new RegExp(String.raw`${LEAD}${FIRST_DAY}\s+(\p{L}+)\.?\s*${JOINER}\s*$`, "u"), day: 2, month: 3 },
  { pattern: new RegExp(String.raw`${LEAD}(\p{L}+)\.?\s+${FIRST_DAY},?\s*${JOINER}\s*$`, "u"), day: 3, month: 2 },
];
const YEAR_AFTER = /^,\s*\d{4}(?![\p{L}\p{N}])(?![./-]\p{N})/u;
const REST_OF_DATE =
  /^\.?\s+(?:\p{Ll}{1,3}\s+)?(\p{L}+)\.?(?:\s+\p{Ll}{1,3})?,?\s+(?:\d{4}|\d{2})(?![\p{L}\p{N}])(?![./-]\p{N})/u;

export function inDayRange(date: CalendarDay, inText: DateInText): boolean {
  const [first] = inText.order;
  if (first === "month") return endsInLastDay(inText) || followsFirstDate(date, inText);
  if (first === "day") return endsInFirstDay(date, inText);
  return false;
}

function endsInLastDay({ parts, between, after }: DateInText): boolean {
  if (parts[2]?.length !== 2) return false;
  const [monthToDay, dayToYear] = between;
  const tight = /^[-/]$/.test(dayToYear) && monthToDay !== dayToYear;
  const yearAfter = !/^\s+$/.test(dayToYear) && YEAR_AFTER.test(after);
  return tight || yearAfter;
}

function endsInFirstDay(date: CalendarDay, inText: DateInText): boolean {
  const { before, hasWeekday } = inText;
  const endings = hasWeekday ? [] : FIRST_DAY_ENDINGS;
  const joinedDay = endings.map((ending) => ending.exec(before)?.[1]).find((day) => day !== undefined);
  if (joinedDay !== undefined) return Number(joinedDay) < date.day;
  if (followsFirstDate(date, inText)) return true;
  if (!hasWeekday) return false;
  const [, weekday = "", day = ""] = FIRST_DAY_WITH_WEEKDAY.exec(before) ?? [];
  const firstDay = { ...date, day: Number(day) };
  return firstDay.day < date.day && isWeekdayOf(weekday, firstDay);
}

function followsFirstDate(date: CalendarDay, { before, language }: DateInText): boolean {
  const first = firstDateIn(before, { year: date.year, language });
  if (first === undefined) return false;
  const earlier = first.day.month < date.month || (first.day.month === date.month && first.day.day < date.day);
  const leads = first.lead === "" || isWeekdayOf(first.lead, first.day);
  return earlier && leads;
}

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

function monthIndexIn(word: string, language: string): number | undefined {
  const base = new Intl.Locale(language).language;
  return namesOf(word, "month").find((name) => new Intl.Locale(name.language).language === base)?.index;
}

export function yearIsAnotherDatesDay({
  order,
  parts,
  between,
  after,
}: Pick<DateInText, "order" | "parts" | "between" | "after">): boolean {
  const dashed = order[2] === "year" && parts[2]?.length === 2 && /^\s*-\s*$/.test(between[1]);
  return dashed && isNameOf(REST_OF_DATE.exec(after)?.[1] ?? "", "month");
}

function isWeekdayOf(word: string, day: CalendarDay): boolean {
  const index = weekdayOf(day);
  return namesOf(word, "weekday").some((name) => name.index === index);
}
