import { describe, expect, test } from "vitest";
import { anAccount } from "../fixtures";
import { mustSignIn, signedInAs, tokenIn } from "./account";

describe("tokenIn", () => {
  test("a sign-in link's token is what follows its #, with or without a slash after the path", () => {
    expect(tokenIn("/sign-in", "#abc.def")).toBe("abc.def");
    expect(tokenIn("/sign-in/", "#abc.def")).toBe("abc.def");
  });

  test("another page, or the sign-in page with nothing after its #, carries none", () => {
    expect(tokenIn("/", "#abc.def")).toBeNull();
    expect(tokenIn("/sign-in", "")).toBeNull();
    expect(tokenIn("/sign-in", "#")).toBeNull();
  });
});

describe("mustSignIn", () => {
  test("only a browser not signed in, on a server open to invited people only, must sign in first", () => {
    const stranger = anAccount({ invite_only: true });
    const invited = anAccount({ invite_only: true, signed_in: { email: "a@example.com", is_unlimited: false } });
    expect(mustSignIn({ kind: "read", state: stranger })).toBe(true);
    expect(mustSignIn({ kind: "read", state: invited })).toBe(false);
    expect(mustSignIn({ kind: "read", state: anAccount() })).toBe(false);
    // Asking, or failed to ask: the landing shows, and the upload says why if it's refused.
    expect(mustSignIn({ kind: "asking" })).toBe(false);
    expect(mustSignIn({ kind: "failed", detail: "Couldn't reach the server." })).toBe(false);
  });
});

test("signedInAs names the address signed in, and nothing where none is", () => {
  const state = anAccount({ signed_in: { email: "a@example.com", is_unlimited: false } });
  expect(signedInAs({ kind: "read", state })).toBe("a@example.com");
  expect(signedInAs({ kind: "read", state: anAccount() })).toBeNull();
  expect(signedInAs({ kind: "asking" })).toBeNull();
});
