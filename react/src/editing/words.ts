// Words the browser puts together: the server's `sentences`, sent unfilled, with their facts
// put in; and the plural forms of the browser's own labels.
import type { CountedSentence } from "../api/types";

export type Facts = Record<string, string | number | readonly string[]>;

// How a list goes into a sentence, as the server joins it (core/app/words, join_chars and join_letters).
const JOINS: Record<string, string> = { chars: " or ", letters: " " };

const ONE_DECIMAL = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
// Counts as the sentences' language writes them: English's, until the reply says its language.
const WHOLE = new Intl.NumberFormat("en");

/**
 * `sentence` with each {placeholder} filled; one with no fact stays as written. A sentence a reading persisted by an
 * earlier build lacks says nothing until the server's is read again (`readStoredAgain`): what's done never waits on it.
 */
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

/** A sentence of the server's with no facts to fill, or nothing where a reading persisted by an earlier build lacks it. */
export function sentenceOrNothing(sentence: string | undefined): string {
  return sentence ?? "";
}

// Draws nothing on its own: a space, an invisible mark, or an accent alone, which would sit on the space before it.
const DRAWS_NOTHING = /^[\p{C}\p{Z}\s\p{Mn}\p{Me}]$/u;

// Punctuation or a symbol, quoted as en.toml's `quoted` quotes it, so it reads as a character, not the sentence's.
const QUOTED = /^[\p{P}\p{S}]$/u;

/**
 * A character as a person can see it: its name in `names` (the server's `sentences.characters`) when it draws
 * nothing alone or looks like another letter, else itself, quoted if punctuation or a symbol. `_visible` in
 * core/app/words. One the catalog doesn't name, the server calls by Unicode's name; the browser hasn't got it,
 * so says its code point.
 */
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

/** The word a count takes: "change" for 1, "changes" for 3. */
export function formOf(count: number, forms: { one: string; other: string }): string {
  return PLURAL.select(count) === "one" ? forms.one : forms.other;
}

/** The form of the server's sentence `forms` a count of `count` takes, its {count} filled: "Changed 3 places." */
export function fillCounted(forms: CountedSentence | undefined, count: number): string {
  // Lacked by a reading persisted by an earlier build, as `fill` says.
  if (forms === undefined) return "";
  return fill(formOf(count, forms), { count: WHOLE.format(count) });
}

/** An interface label for a count: "1 change", "3 changes". */
export function counted(count: number, forms: { one: string; other: string }): string {
  return `${count} ${formOf(count, forms)}`;
}
