import { describe, expect, test } from "vitest";
import { SENTENCES } from "../fixtures";
import { counted, fill, fillCounted, shownAs } from "./words";

describe("filling the server's sentences", () => {
  test("missing letters are joined with 'or', as the server says them", () => {
    const sentence = "no {chars} in this font, so the line is drawn in {font}";
    expect(fill(sentence, { chars: ["é", "ö"], font: "Liberation Serif Regular" })).toBe(
      "no é or ö in this font, so the line is drawn in Liberation Serif Regular",
    );
  });

  test("a width is said to one decimal place", () => {
    expect(fill("{delta_pt} pt too long", { delta_pt: 4.36 })).toBe("4.4 pt too long");
    // A half rounds up, as the server writes it (core/app/words).
    expect(fill("{delta_pt} pt too long", { delta_pt: 2.25 })).toBe("2.3 pt too long");
    expect(fill("{delta_pt} pt too long", { delta_pt: 1.45 })).toBe("1.5 pt too long");
  });

  test("a placeholder with no fact is left as it is", () => {
    expect(fill("{delta_pt} pt too long", {})).toBe("{delta_pt} pt too long");
  });

  // The server's own cases, test_a_character_that_draws_nothing_is_named (tests/core/test_words.py).
  test.each([
    ["\u202f", "narrow no-break space"],
    ["\u00ad", "optional hyphen"],
    ["\x01", "U+0001"],
    ["\u0301", "acute accent"],
  ])("a character that draws nothing is named: %j is %s", (character, name) => {
    expect(shownAs(character, SENTENCES.characters)).toBe(name);
  });

  // The server's own case, test_punctuation_or_a_symbol_is_quoted_so_it_reads_as_a_character_and_a_letter_isnt.
  test("punctuation or a symbol is quoted, so it reads as a character, and a letter isn't", () => {
    expect(["(", "Z", "€"].map((character) => shownAs(character, SENTENCES.characters))).toEqual(["“(”", "Z", "“€”"]);
  });

  // The server's own case, test_a_letter_that_looks_like_another_is_named_so_the_two_read_apart.
  test("a letter that looks like another is named, so the two read apart", () => {
    expect(["I", "l", "O", "0", "1"].map((character) => shownAs(character, SENTENCES.characters))).toEqual([
      "capital I",
      "lowercase l",
      "capital O",
      "zero",
      "1",
    ]);
  });

  test("a letter that draws is itself, and one only Unicode names is said by its code point", () => {
    expect(shownAs("é", SENTENCES.characters)).toBe("é");
    // The server says "three-per-em space": the browser has no table of Unicode's names.
    expect(shownAs("\u2004", SENTENCES.characters)).toBe("U+2004");
  });

  test("a count takes its plural from the language's rules, not an added s", () => {
    expect(counted(1, { one: "change", other: "changes" })).toBe("1 change");
    expect(counted(2, { one: "change", other: "changes" })).toBe("2 changes");
    expect(counted(1234, { one: "change", other: "changes" })).toBe("1,234 changes");
  });
});

describe("the forms a sentence with a count takes", () => {
  test("the form the count takes is filled, the count written as numbers are", () => {
    expect(fillCounted(SENTENCES.changed_places, 1)).toBe("Changed 1 place.");
    expect(fillCounted(SENTENCES.changed_places, 1234)).toBe("Changed 1,234 places.");
  });

  test("a sentence a reading persisted by an earlier build lacks is nothing, whatever the count", () => {
    expect(fillCounted(undefined, 3)).toBe("");
  });

  test("English counts with the two the server sends, one and other, so every count has its form", () => {
    const named = new Intl.PluralRules("en").resolvedOptions().pluralCategories;
    expect(named.toSorted()).toEqual(["one", "other"]);
  });
});
