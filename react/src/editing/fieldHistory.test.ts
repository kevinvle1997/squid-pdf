import { expect, test } from "vitest";
import { type FieldHistory, NO_TYPING, recorded, redone, type Typing, undone } from "./fieldHistory";

const at = (words: string, caret: number): Typing => ({ runs: [words], selected: [caret, caret] });

/** `history` after typing each letter of `word` at the end of `words`, one at a time. */
function typing(history: FieldHistory, words: string, word: string): FieldHistory {
  let after = history;
  let now = words;
  for (const letter of word) {
    after = recorded(after, { before: at(now, now.length), typed: letter, caretAfter: now.length + 1 });
    now += letter;
  }
  return after;
}

test("a word typed is undone as one, a space and the next word as their own, and redo brings them back in turn", () => {
  const history = typing(typing(typing(NO_TYPING, "a", "bc"), "abc", " "), "abc ", "de");
  const back = undone(history, at("abc de", 6));
  expect(back?.to).toEqual(at("abc ", 4));
  const further = back && undone(back.history, back.to);
  expect(further?.to).toEqual(at("abc", 3));
  const first = further && undone(further.history, further.to);
  expect(first?.to).toEqual(at("a", 1));
  expect(first && undone(first.history, first.to)).toBeNull();
  const forward = first && redone(first.history, first.to);
  expect(forward?.to).toEqual(at("abc", 3));
});

test("typing after an undo ends what redo could bring back, and a letter typed elsewhere starts its own step", () => {
  const history = typing(NO_TYPING, "a", "b");
  const back = undone(history, at("ab", 2));
  const typed = recorded(back?.history ?? NO_TYPING, { before: at("a", 0), typed: "x", caretAfter: 1 });
  expect(redone(typed, at("xa", 1))).toBeNull();
  const elsewhere = recorded(typing(NO_TYPING, "a", "b"), { before: at("ab", 0), typed: "c", caretAfter: 1 });
  expect(elsewhere.past).toHaveLength(2);
});
