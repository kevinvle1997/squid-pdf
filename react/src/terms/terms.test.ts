import { expect, test } from "vitest";
import { TERMS } from "../fixtures";
import { isTermsPath, TERMS_PATH, termsHrefOf } from "./terms";

test("the terms' page is found with a trailing slash or none, and nowhere else", () => {
  expect(["/terms", "/terms/", "/", "/termsx", "/terms/more"].map(isTermsPath)).toEqual([
    true,
    true,
    false,
    false,
    false,
  ]);
});

test("the landing links the terms once the server says its deployment has them, and only then", () => {
  expect(termsHrefOf({ kind: "read", terms: TERMS })).toBe(TERMS_PATH);
  expect(termsHrefOf({ kind: "failed", detail: "Couldn't reach the server." })).toBeNull();
  expect(termsHrefOf({ kind: "none" })).toBeNull();
  expect(termsHrefOf({ kind: "asking" })).toBeNull();
});
