import { describe, expect, test } from "vitest";
import { carriesFiles } from "./dropped";

describe("a drag over the editor", () => {
  test("carries files when it's from the desktop, not when it's text or a link", () => {
    expect(carriesFiles({ types: ["Files"] })).toBe(true);
    expect(carriesFiles({ types: ["text/plain", "text/uri-list"] })).toBe(false);
    expect(carriesFiles(null)).toBe(false);
  });
});
