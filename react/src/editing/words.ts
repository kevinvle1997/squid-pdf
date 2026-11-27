import type { CountedSentence } from "../api/types";

export type Facts = Record<string, string | number | readonly string[]>;

// How a list goes into a sentence, as the server joins it (core/app/words, join_chars and join_letters).
const JOINS: Record<string, string> = { chars: " or ", letters: " " };

const ONE_DECIMAL = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const WHOLE = new Intl.NumberFormat("en");

export function fill(sentence: string | undefined, facts: Facts): string {
  if (sentence === undefined) return "";
  return sentence.replace(/\{(\w+)\}/g, (placeholder: string, key: string) => {
    const fact = facts[key];
    if (fact === undefined) return placeholder;
    if (typeof fact === "number") return ONE_DECIMAL.format(fact);
    if (typeof fact === "string") return fact;
    return fact.join(JOINS[key] ?? " ");
  });
}

export function sentenceOrNothing(sentence: string | undefined): string {
  return sentence ?? "";
}

// Draws nothing on its own: a space, an invisible mark, or an accent alone, which would sit on the space before it.
const DRAWS_NOTHING = /^[\p{C}\p{Z}\s\p{Mn}\p{Me}]$/u;

const QUOTED = /^[\p{P}\p{S}]$/u;

/** The server names a character its catalog doesn't by Unicode's name; the browser hasn't that table, so says its code point. */
export function shownAs(character: string, names: Readonly<Record<string, string>>): string {
  const named = names[character];
  if (named !== undefined) return named;
  if (QUOTED.test(character)) return `\u201c${character}\u201d`;
  if (!DRAWS_NOTHING.test(character)) return character;
  const codePoint = character.codePointAt(0) ?? 0;
  return `U+${codePoint.toString(16).toUpperCase().padStart(4, "0")}`;
}

// The forms on the wire are English's, chosen by its rules, until a language with other forms widens the wire.
const PLURAL = new Intl.PluralRules("en");

export function formOf(count: number, forms: { one: string; other: string }): string {
  return PLURAL.select(count) === "one" ? forms.one : forms.other;
}

export function fillCounted(forms: CountedSentence | undefined, count: number): string {
  if (forms === undefined) return "";
  return fill(formOf(count, forms), { count: WHOLE.format(count) });
}

export function counted(count: number, forms: { one: string; other: string }): string {
  return `${WHOLE.format(count)} ${formOf(count, forms)}`;
}
