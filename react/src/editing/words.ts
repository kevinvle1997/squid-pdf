// Filling in the server's sentences. The server writes every sentence about the document
// and sends them unfilled in `copy`; the browser only puts the facts in.

export type Facts = Record<string, string | number | readonly string[]>;

// How a list goes into a sentence, as the server joins it (core/words, join_chars and join_letters).
const JOINS: Record<string, string> = { chars: " or ", letters: " " };

const ONE_PLACE = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** `sentence` with each {placeholder} filled; one with no fact stays as written. */
export function fill(sentence: string, facts: Facts): string {
  return sentence.replace(/\{(\w+)\}/g, (placeholder: string, key: string) => {
    const fact = facts[key];
    if (fact === undefined) return placeholder;
    if (typeof fact === "number") return ONE_PLACE.format(fact);
    if (typeof fact === "string") return fact;
    return fact.join(JOINS[key] ?? " ");
  });
}

const PLURAL = new Intl.PluralRules("en");

/** An interface label for a count: "1 change", "3 changes". */
export function counted(count: number, forms: { one: string; other: string }): string {
  const form = PLURAL.select(count) === "one" ? forms.one : forms.other;
  return `${count} ${form}`;
}
