import { afterEach, describe, expect, test, vi } from "vitest";
import { ProblemError } from "./api/client";
import { SOMETHING_WENT_WRONG, sentenceOf } from "./bugs";
import { aProblem } from "./fixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("what a failure says", () => {
  test("a Problem says the server's own sentence, and isn't logged as a bug", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(sentenceOf(new ProblemError(aProblem(503, "The server is busy.")))).toBe("The server is busy.");
    expect(logged).not.toHaveBeenCalled();
  });

  test("anything else is a bug: logged, and said in the browser's words", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const bug = new TypeError("undefined is not a function");
    expect(sentenceOf(bug)).toBe(SOMETHING_WENT_WRONG);
    expect(logged).toHaveBeenCalledWith(bug);
  });
});
