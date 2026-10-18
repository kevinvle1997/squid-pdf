import { expect, test } from "vitest";
import { classes } from "./classes";

test("a class list keeps the names given, in order, and leaves out the rest", () => {
  expect(classes("button", "primary", undefined)).toBe("button primary");
  expect(classes("image", false, null, "missing")).toBe("image missing");
  expect(classes("span")).toBe("span");
});
