// Month and weekday names in every language Intl has dates for, worked out from Intl.DateTimeFormat, so no
// table of names ships: which languages name a word, and a month or a weekday named again in one of them.
import { LEARN_BATCH, LEARN_SLICE_MS } from "./constants";

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
const KINDS: readonly NameKind[] = ["month", "weekday"];
// A Sunday, the first of the week as Intl numbers its days.
const A_SUNDAY = Date.UTC(2000, 0, 2);
const DAY_MS = 86_400_000;

/** Every name of the languages learnt so far, by the name in lower case, in the order they were learnt. */
const byName = new Map<string, DateName[]>();
const namesIn = new Map<string, readonly DateName[]>();
const learnt = new Set<string>();
let everyLanguage: Promise<void> | undefined;

/**
 * Every language Intl has dates for, learnt in slices between other work: those with a two letter code, then
 * those with three. Why: Intl lists no languages, and asking it of every code at once holds the page up.
 */
export function learnEveryLanguage(): Promise<void> {
  everyLanguage ??= learnInSlices();
  return everyLanguage;
}

async function learnInSlices(): Promise<void> {
  const steps = learningSteps();
  // Each slice a task of its own, the first too: whatever asked goes on first.
  do {
    await new Promise((nextTask) => setTimeout(nextTask));
  } while (learntASlice(steps));
}

/**
 * Learn for one slice's time, stopping before a step as long as the longest so far would run past it: false once
 * there's nothing left to learn.
 */
function learntASlice(steps: Generator<void>): boolean {
  const start = performance.now();
  let longest = 0;
  for (let now = start; now - start + longest < LEARN_SLICE_MS; ) {
    if (steps.next().done) return false;
    const stepped = performance.now();
    longest = Math.max(longest, stepped - now);
    now = stepped;
  }
  return true;
}

/**
 * Learning every language, a step at a time, each well inside a slice: asking Intl which of a few codes it has,
 * then each one's forms, a format at a time. Why so small: one ask of Intl can't be split, nor one format.
 */
function* learningSteps(): Generator<void> {
  const twoLetters = ALPHABET.flatMap((first) => ALPHABET.map((second) => first + second));
  const threeLetters = ALPHABET.flatMap((first) => twoLetters.map((rest) => first + rest));
  const codes = [...twoLetters, ...threeLetters];
  for (let at = 0; at < codes.length; at += LEARN_BATCH) {
    const languages = Intl.DateTimeFormat.supportedLocalesOf(codes.slice(at, at + LEARN_BATCH));
    yield;
    for (const language of languages) yield* learningOf(language);
  }
}

/** Learning the names of `language` and of its regions' forms that Intl has, a step a form and a format. */
function* learningOf(language: string): Generator<void> {
  // A three letter code Intl reads as a two letter one's language: "deu" is "de".
  if (learnt.has(language)) return;
  learnt.add(language);
  for (const form of [language, ...REGIONS.map((region) => `${language}-${region}`)]) {
    const own = form === language || isOwnForm(form);
    yield;
    if (own) yield* learningNamesIn(form);
  }
}

/** Learning the names in `language`, a format at a time, then indexing them by name. */
function* learningNamesIn(language: string): Generator<void> {
  const named: DateName[] = [];
  for (const width of WIDTHS) {
    for (const kind of KINDS) {
      named.push(...namedInDates(language, { kind, width }));
      yield;
    }
  }
  namesIn.set(language, named);
  for (const name of named) {
    const key = name.name.toLowerCase();
    const same = byName.get(key);
    if (same === undefined) byName.set(key, [name]);
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

/** The names `word` is, of `kind`, in every language learnt so far, in any case. */
export function namesOf(word: string, kind: NameKind): readonly DateName[] {
  return (byName.get(word.normalize("NFC").toLowerCase()) ?? []).filter((name) => name.kind === kind);
}

/** The twelve months' names in `language`, then its seven weekdays', long then short, as each is written in a date. */
function namesInLanguage(language: string): readonly DateName[] {
  const cached = namesIn.get(language);
  if (cached !== undefined) return cached;
  const named = WIDTHS.flatMap((width) => KINDS.flatMap((kind) => namedInDates(language, { kind, width })));
  namesIn.set(language, named);
  return named;
}

/** The names `namesInDates` finds, where `language` writes them in letters: some write their months as numbers. */
function namedInDates(language: string, which: { kind: NameKind; width: NameWidth }): DateName[] {
  return namesInDates(language, which).filter((name) => /\p{L}/u.test(name.name));
}

/** Every month's or weekday's name in `language`, `width` long, as a date writes it: one format, asked twelve or seven times. */
function namesInDates(language: string, { kind, width }: { kind: NameKind; width: NameWidth }): DateName[] {
  const options: Intl.DateTimeFormatOptions = { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" };
  const format = new Intl.DateTimeFormat(language, { ...options, [kind]: width });
  const indexes = kind === "month" ? [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] : [0, 1, 2, 3, 4, 5, 6];
  return indexes.map((index) => {
    const date = kind === "month" ? Date.UTC(2000, index - 1, 14) : A_SUNDAY + index * DAY_MS;
    const name = format.formatToParts(date).find((part) => part.type === kind)?.value ?? "";
    return { language, kind, index, width, name };
  });
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
