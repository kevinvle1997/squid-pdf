// Intl says which form a day takes in a language, but has no words for the suffixes.
import { caseOf, inCase, type LetterCase } from "./dateNames";

export interface DayOrdinal {
  readonly day: number;
  readonly suffix: string;
}

export interface OrdinalWord {
  readonly language: string;
  readonly letterCase: LetterCase;
}

// A form not listed takes none, as French writes "1er mars" but "2 mars"; a suffix no language here writes is no date's.
const SUFFIXES: Readonly<Record<string, Partial<Record<Intl.LDMLPluralRule, string>>>> = {
  en: { one: "st", two: "nd", few: "rd", other: "th" },
  fr: { one: "er" },
};

function suffixIn(language: string, day: number): string | undefined {
  const suffixes = SUFFIXES[new Intl.Locale(language).language];
  if (suffixes === undefined) return undefined;
  return suffixes[new Intl.PluralRules(language, { type: "ordinal" }).select(day)] ?? "";
}

export function writesOrdinal(language: string, { day, suffix }: DayOrdinal): boolean {
  return suffixIn(language, day) === suffix.toLowerCase();
}

export function someLanguageWrites(ordinal: DayOrdinal): boolean {
  return Object.keys(SUFFIXES).some((language) => writesOrdinal(language, ordinal));
}

export function ordinalWordOf(language: string, { day, suffix }: DayOrdinal): OrdinalWord {
  return { language, letterCase: caseOf(suffix, suffixIn(language, day) ?? "") };
}

export function suffixFor(day: number, word: OrdinalWord): string {
  return inCase(suffixIn(word.language, day) ?? "", word);
}
