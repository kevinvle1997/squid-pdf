// Words the browser puts together: the server's sentences, sent unfilled in `copy`, with their facts
// put in; and the plural forms of the browser's own labels.

export type Facts = Record<string, string | number | readonly string[]>;

// How a list goes into a sentence, as the server joins it (core/app/words, join_chars and join_letters).
const JOINS: Record<string, string> = { chars: " or ", letters: " " };

const ONE_DECIMAL = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** `sentence` with each {placeholder} filled; one with no fact stays as written. */
export function fill(sentence: string, facts: Facts): string {
  return sentence.replace(/\{(\w+)\}/g, (placeholder: string, key: string) => {
    const fact = facts[key];
    if (fact === undefined) return placeholder;
    if (typeof fact === "number") return ONE_DECIMAL.format(fact);
    if (typeof fact === "string") return fact;
    return fact.join(JOINS[key] ?? " ");
  });
}

// Draws nothing on its own: a space, an invisible mark, or an accent alone, which would sit on the space before it.
const DRAWS_NOTHING = /^[\p{C}\p{Z}\s\p{Mn}\p{Me}]$/u;

// Punctuation or a symbol, quoted as en.toml's `quoted` quotes it, so it reads as a character, not the sentence's.
const QUOTED = /^[\p{P}\p{S}]$/u;

/**
 * A character as a person can see it: its name in `names` (the server's `copy.characters`) when it draws
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

const PLURAL = new Intl.PluralRules("en");

/** The word a count takes: "change" for 1, "changes" for 3. */
export function formOf(count: number, forms: { one: string; other: string }): string {
  return PLURAL.select(count) === "one" ? forms.one : forms.other;
}

/** An interface label for a count: "1 change", "3 changes". */
export function counted(count: number, forms: { one: string; other: string }): string {
  return `${count} ${formOf(count, forms)}`;
}
