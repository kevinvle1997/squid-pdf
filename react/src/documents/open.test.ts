import { describe, expect, test } from "vitest";
import { shareSent } from "./open";

describe("a file on its way to the server", () => {
  test("is sent by its real share, and once it's all sent, the server is reading it", () => {
    expect(shareSent({ sent: 0, total: 0 })).toBe(0);
    expect(shareSent({ sent: 50, total: 100 })).toBe(0.5);
    expect(shareSent({ sent: 100, total: 100 })).toBeNull();
  });
});
