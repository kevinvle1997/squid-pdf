// A day of the month written as an ordinal, "14th" or "1er": which languages write a day so, and the suffix
// another day takes. Intl says which form a day takes in a language, but has no words for the suffixes.
import { caseOf, inCase, type LetterCase } from "./dateNames";

/** A day as the page wrote it with letters after its number, perhaps an ordinal's suffix: "14th" is day 14 with "th". */
export interface DayOrdinal {
  readonly day: number;
  readonly suffix: string;
}

/** How the page wrote a day's suffix: in which language, and in what case. */
export interface OrdinalWord {
  readonly language: string;
  readonly letterCase: LetterCase;
}

// The suffix after a day in a date, by the form Intl gives the day; a form not listed takes none, as French writes
// "1er mars" but "2 mars". A day with a suffix no language here writes is no date's: no editor guesses it.
const SUFFIXES: Readonly<Record<string, Partial<Record<Intl.LDMLPluralRule, string>>>> = {
  en: { one: "st", two: "nd", few: "rd", other: "th" },
  fr: { one: "er" },
};

/** The suffix `language` writes after `day`, in lower case, "" for none; undefined where it writes none of them. */
function suffixIn(language: string, day: number): string | undefined {
  const suffixes = SUFFIXES[new Intl.Locale(language).language];
  if (suffixes === undefined) return undefined;
  return suffixes[new Intl.PluralRules(language, { type: "ordinal" }).select(day)] ?? "";
}

/** Whether `language` writes `ordinal`'s day with its suffix, in any case. */
export function writesOrdinal(language: string, { day, suffix }: DayOrdinal): boolean {
  return suffixIn(language, day) === suffix.toLowerCase();
}

/** Whether some language writes `ordinal`'s day with its suffix. */
export function someLanguageWrites(ordinal: DayOrdinal): boolean {
  return Object.keys(SUFFIXES).some((language) => writesOrdinal(language, ordinal));
}

/** How the page wrote `ordinal`, read in `language`. */
export function ordinalWordOf(language: string, { day, suffix }: DayOrdinal): OrdinalWord {
  return { language, letterCase: caseOf(suffix, suffixIn(language, day) ?? "") };
}

/** The suffix after `day`, written as `word` says, the page's way. */
export function suffixFor(day: number, word: OrdinalWord): string {
  return inCase(suffixIn(word.language, day) ?? "", word);
}
