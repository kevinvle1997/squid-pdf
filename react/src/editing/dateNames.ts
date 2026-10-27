// Month and weekday names in every language Intl has dates for, worked out from Intl.DateTimeFormat, so no
// table of names ships: which languages name a word, and a month or a weekday named again in one of them.

export type NameWidth = "long" | "short";
export type NameKind = "month" | "weekday";
export type LetterCase = "as-the-language" | "upper" | "lower" | "title";

/** One name in one language, as it's written inside a date, which some languages decline. */
export interface DateName {
  readonly language: string;
  readonly kind: NameKind;
  readonly index: number; // a month from 1, a weekday from 0, Sunday
  readonly width: NameWidth;
  readonly name: string;
}

/** How the page wrote a month or a weekday by name: in which language, how long, and in what case. */
export interface NameWord {
  readonly language: string;
  readonly width: NameWidth;
  readonly letterCase: LetterCase;
}

const ALPHABET = [..."abcdefghijklmnopqrstuvwxyz"];
// The world's, Europe's and Latin America's forms of a language, where Intl has them: en-001 writes "14 Sept 2026".
const REGIONS = ["001", "150", "419"];
const WIDTHS: readonly NameWidth[] = ["long", "short"];
// A Sunday, the first of the week as Intl numbers its days.
const A_SUNDAY = Date.UTC(2000, 0, 2);
const DAY_MS = 86_400_000;

/** Every name of the languages learnt so far, by the name in lower case, in the order they were learnt. */
let byName: Map<string, DateName[]> | undefined;
const namesIn = new Map<string, readonly DateName[]>();
const learnt = new Set<string>();
// Who hears once every language is learnt; null once they all are.
let waiting: (() => void)[] | null = [];
let learning = false;

/** The names of every language with a two letter code that Intl has dates for, and its regions' forms. */
function names(): Map<string, DateName[]> {
  if (byName !== undefined) return byName;
  byName = new Map();
  const twoLetters = ALPHABET.flatMap((first) => ALPHABET.map((second) => first + second));
  for (const language of Intl.DateTimeFormat.supportedLocalesOf(twoLetters)) learn(language);
  return byName;
}

/**
 * Learn the languages with a two letter code now, and the rest, with three, a first letter at a time between
 * other work, then call `onLearnt`. Why: Intl lists no languages, and asking it of every three letter code at
 * once holds the page up.
 */
export function learnEveryLanguage(onLearnt: () => void): void {
  names();
  if (waiting === null) return;
  waiting.push(onLearnt);
  if (learning) return;
  learning = true;
  const firstLetters = [...ALPHABET];
  const next = () => {
    const first = firstLetters.shift();
    if (first === undefined) {
      const heard = waiting ?? [];
      waiting = null;
      for (const hear of heard) hear();
      return;
    }
    const codes = ALPHABET.flatMap((second) => ALPHABET.map((third) => first + second + third));
    for (const language of Intl.DateTimeFormat.supportedLocalesOf(codes)) learn(language);
    setTimeout(next);
  };
  setTimeout(next);
}

/** Index the names of `language` and of its regions' forms that Intl has. */
function learn(language: string): void {
  // A three letter code Intl reads as a two letter one's language: "deu" is "de".
  if (learnt.has(language)) return;
  learnt.add(language);
  const index = names();
  const forms = [language, ...REGIONS.map((region) => `${language}-${region}`).filter(isOwnForm)];
  for (const name of forms.flatMap(namesInLanguage)) {
    const key = name.name.toLowerCase();
    const same = index.get(key);
    if (same === undefined) index.set(key, [name]);
    else same.push(name);
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

/** The names `word` is, of `kind`, in every language learnt, in any case. */
export function namesOf(word: string, kind: NameKind): readonly DateName[] {
  return (names().get(word.normalize("NFC").toLowerCase()) ?? []).filter((name) => name.kind === kind);
}

/** The twelve months' names in `language`, then its seven weekdays', long then short, as each is written in a date. */
function namesInLanguage(language: string): readonly DateName[] {
  const cached = namesIn.get(language);
  if (cached !== undefined) return cached;
  const all = WIDTHS.flatMap((width) => [
    ...Array.from({ length: 12 }, (_, index) => nameOf(language, { kind: "month", width, index: index + 1 })),
    ...Array.from({ length: 7 }, (_, index) => nameOf(language, { kind: "weekday", width, index })),
  ]);
  // A language that writes its months as numbers has no names to find.
  const named = all.filter((name) => /\p{L}/u.test(name.name));
  namesIn.set(language, named);
  return named;
}

function nameOf(
  language: string,
  { kind, width, index }: { kind: NameKind; width: NameWidth; index: number },
): DateName {
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" };
  const date = kind === "month" ? Date.UTC(2000, index - 1, 14) : A_SUNDAY + index * DAY_MS;
  const format = new Intl.DateTimeFormat(language, { ...options, [kind]: width });
  const name = format.formatToParts(date).find((part) => part.type === kind)?.value ?? "";
  return { language, kind, index, width, name };
}

/** The words `language` writes between a date's parts with its months `width` long: "de" in "14 de marzo de 2026". */
export function wordsBetweenIn(language: string, width: NameWidth): readonly string[] {
  const format = new Intl.DateTimeFormat(language, { day: "numeric", month: width, year: "numeric", timeZone: "UTC" });
  return format
    .formatToParts(Date.UTC(2000, 2, 14))
    .filter((part) => part.type === "literal")
    .flatMap((part) => part.value.toLowerCase().match(/\p{L}+/gu) ?? []);
}

/** The `kind` numbered `index` named as `word` says, the page's way. */
export function nameFor(kind: NameKind, index: number, { language, width, letterCase }: NameWord): string {
  const own = namesInLanguage(language).find(
    (name) => name.kind === kind && name.index === index && name.width === width,
  );
  return inCase(own?.name ?? "", { letterCase, language });
}

function inCase(name: string, { letterCase, language }: { letterCase: LetterCase; language: string }): string {
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
