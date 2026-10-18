import { describe, expect, test } from "vitest";
import { type FindOptions, matchesIn, replacedIn } from "./matching";

describe("matching", () => {
  const ANY = { matchCase: false, wholeWords: false };
  /** `text` with what reads `find` changed `to`, as "Find" matches with `options`. */
  const replaced = (text: string, { find, to, ...options }: { find: string; to: string } & Partial<FindOptions>) =>
    replacedIn(text, matchesIn(text, find, { ...ANY, ...options }), to);

  test("a space, quote or dash the keyboard can't type matches the one it can", () => {
    const line = "Paid on 14\u00a0March to the Client\u2019s bank \u2013 in full";
    expect(replaced(line, { find: "14 March", to: "2 April" })).toBe(
      "Paid on 2\u00a0April to the Client\u2019s bank \u2013 in full",
    );
    expect(replaced(line, { find: "Client's", to: "Customer's" })).toBe(
      "Paid on 14\u00a0March to the Customer\u2019s bank \u2013 in full",
    );
    expect(replaced(line, { find: "bank - in", to: "bank, in" })).toBe(
      "Paid on 14\u00a0March to the Client\u2019s bank, in full",
    );
  });

  test("a change is spelled as the page spelled what it replaces: its spaces, quotes and dashes, where all were one kind", () => {
    expect(replaced("from 2019\u20132020", { find: "2019-2020", to: "2020-2021" })).toBe("from 2020\u20132021");
    // A no-break space and a plain one in what was found: nothing to say which the change wants.
    expect(replaced("14\u00a0March 2026", { find: "14 March 2026", to: "2 April 2027" })).toBe("2 April 2027");
    expect(replaced("the Client\u2019s", { find: "Client\u2019s", to: "Customer's" })).toBe("the Customer\u2019s");
  });

  test("letters of either case match, unless Match case", () => {
    expect(replaced("March and march", { find: "MARCH", to: "June" })).toBe("June and June");
    expect(replaced("March and march", { find: "march", to: "june", matchCase: true })).toBe("March and june");
  });

  test("a match inside a word counts, unless Whole words", () => {
    expect(replaced("Ann signed the Annual report", { find: "Ann", to: "Bea" })).toBe("Bea signed the Beaual report");
    expect(replaced("Ann signed the Annual report", { find: "Ann", to: "Bea", wholeWords: true })).toBe(
      "Bea signed the Annual report",
    );
  });

  test("a ligature reads as its letters, and is only ever changed whole", () => {
    expect(replaced("\ufb01nance", { find: "finance", to: "funds" })).toBe("funds");
    // Half of one would leave the other letter's half behind.
    expect(matchesIn("\ufb01nance", "f", ANY)).toEqual([]);
  });

  test("an unseen hyphen or space between letters doesn't stop them matching, and goes with what's changed", () => {
    expect(replaced("the infor\u00admation sheet", { find: "information", to: "data" })).toBe("the data sheet");
    expect(replaced("a\u200bb", { find: "ab", to: "c" })).toBe("c");
  });

  test("nothing to find finds nothing", () => {
    expect(matchesIn("anything", "", ANY)).toEqual([]);
    expect(matchesIn("anything", "\u00ad", ANY)).toEqual([]);
  });
});
