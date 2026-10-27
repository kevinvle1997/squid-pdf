// Month names in every language Intl has dates for, worked out from Intl.DateTimeFormat, so no table of
// names ships: which month a word names in a date, and a month named again as the page named another.

export type MonthWidth = "long" | "short";
export type LetterCase = "as-the-language" | "upper" | "lower" | "title";

/** How the page wrote a month by name: in which language, how long, and in what case. */
export interface MonthWord {
  readonly language: string;
  readonly width: MonthWidth;
  readonly letterCase: LetterCase;
}

/** A month's name in one language: the form it takes inside a date, which some languages decline. */
interface MonthName {
  readonly language: string;
  readonly month: number;
  readonly width: MonthWidth;
  readonly name: string;
}

const ALPHABET = [..."abcdefghijklmnopqrstuvwxyz"];
// The world's, Europe's and Latin America's forms of a language, where Intl has them: en-001 writes "14 Sept 2026".
const REGIONS = ["001", "150", "419"];

/** Every month name of the languages learnt so far, by the name in lower case, in the order they were learnt. */
let monthIndex: Map<string, MonthName[]> | undefined;
const namesIn = new Map<string, readonly MonthName[]>();
const chosen = new Map<string, MonthName | null>();
const learnt = new Set<string>();
let learning = false;

/** The month names of every language with a two letter code that Intl has dates for, and its regions' forms. */
function monthNames(): Map<string, MonthName[]> {
  if (monthIndex !== undefined) return monthIndex;
  monthIndex = new Map();
  const twoLetters = ALPHABET.flatMap((first) => ALPHABET.map((second) => first + second));
  for (const language of Intl.DateTimeFormat.supportedLocalesOf(twoLetters)) learn(language);
  return monthIndex;
}

/**
 * Learn the rest of the languages Intl has dates for, those with a three letter code, a first letter at a
 * time, between other work. Why: Intl lists no languages, and asking it of every three letter code at once
 * holds the page up.
 */
export function learnEveryLanguage(): void {
  if (learning) return;
  learning = true;
  monthNames();
  const firstLetters = [...ALPHABET];
  const next = () => {
    const first = firstLetters.shift();
    if (first === undefined) return;
    const codes = ALPHABET.flatMap((second) => ALPHABET.map((third) => first + second + third));
    for (const language of Intl.DateTimeFormat.supportedLocalesOf(codes)) learn(language);
    // A word read before may name a month in a language just learnt.
    chosen.clear();
    setTimeout(next);
  };
  setTimeout(next);
}

/** Index the month names of `language` and of its regions' forms that Intl has. */
function learn(language: string): void {
  // A three letter code Intl reads as a two letter one's language: "deu" is "de".
  if (learnt.has(language)) return;
  learnt.add(language);
  const index = monthNames();
  const forms = [language, ...REGIONS.map((region) => `${language}-${region}`).filter(isOwnForm)];
  for (const form of forms) {
    for (const month of monthNamesIn(form)) {
      const key = month.name.toLowerCase();
      const same = index.get(key);
      if (same === undefined) index.set(key, [month]);
      else same.push(month);
    }
  }
}

/** Whether Intl has dates of `locale`'s own, rather than its language's. */
function isOwnForm(locale: string): boolean {
  try {
    return new Intl.DateTimeFormat(locale).resolvedOptions().locale === locale;
  } catch {
    // A region Intl can't pair with this language's script.
    return false;
  }
}

/**
 * The month `word` names in the date `whole`: in a language that writes it just so before one that writes it
 * in another case, then one whose own dates read as `whole` does, then the first learnt.
 */
export function monthNamed(word: string, shape: { whole: string; day: number; year: string }): MonthName | undefined {
  const key = `${word}\n${shape.whole}`;
  const cached = chosen.get(key);
  if (cached !== undefined) return cached ?? undefined;
  const named = monthNames().get(word.normalize("NFC").toLowerCase()) ?? [];
  const asWritten = named.filter((month) => month.name === word);
  const candidates = asWritten.length > 0 ? asWritten : named;
  const found = candidates.find((month) => readsAs(month, shape)) ?? candidates[0];
  chosen.set(key, found ?? null);
  return found;
}

// Spaces of every kind read as one, as a no-break space between a date's parts is a page's choice.
const ANY_SPACE = /[\s\u00a0\u202f]+/gu;

/** Whether `month`'s language writes the date as `whole` reads, parts, marks and all. */
function readsAs(month: MonthName, { whole, day, year }: { whole: string; day: number; year: string }): boolean {
  const twoDigits = year.length === 2;
  const format = new Intl.DateTimeFormat(month.language, {
    day: "numeric",
    month: month.width,
    year: twoDigits ? "2-digit" : "numeric",
    timeZone: "UTC",
  });
  const own = format.format(Date.UTC(twoDigits ? 2000 + Number(year) : Number(year), month.month - 1, day));
  return own.replace(ANY_SPACE, " ") === whole.replace(ANY_SPACE, " ");
}

/** The twelve months' names in `language`, long then short, as each is written inside a date. */
function monthNamesIn(language: string): readonly MonthName[] {
  const cached = namesIn.get(language);
  if (cached !== undefined) return cached;
  const widths: MonthWidth[] = ["long", "short"];
  const names = widths.flatMap((width) => {
    const format = new Intl.DateTimeFormat(language, {
      day: "numeric",
      month: width,
      year: "numeric",
      timeZone: "UTC",
    });
    return Array.from({ length: 12 }, (_, index) => {
      const name = format.formatToParts(Date.UTC(2000, index, 14)).find((part) => part.type === "month")?.value ?? "";
      return { language, month: index + 1, width, name };
    });
  });
  // A language that writes its months as numbers has no names to find.
  const named = names.filter((month) => /\p{L}/u.test(month.name));
  namesIn.set(language, named);
  return named;
}

/** `month` named as the page named the one it replaces. */
export function monthNameFor(month: number, { language, width, letterCase }: MonthWord): string {
  const name = monthNamesIn(language).find((named) => named.month === month && named.width === width)?.name ?? "";
  switch (letterCase) {
    case "as-the-language":
      return name;
    case "upper":
      return name.toLocaleUpperCase(language);
    case "lower":
      return name.toLocaleLowerCase(language);
    case "title":
      return name.charAt(0).toLocaleUpperCase(language) + name.slice(1);
    default:
      return letterCase satisfies never;
  }
}

/** How `written` sets the case of `name`, the language's own spelling of it. */
export function caseOf(written: string, name: string): LetterCase {
  if (written === name) return "as-the-language";
  if (written === written.toUpperCase()) return "upper";
  if (written === written.toLowerCase()) return "lower";
  return "title";
}
