import { describe, expect, test } from "vitest";
import { aSkipped, aSpanNotice } from "../fixtures";
import { NO_NOTICES, noticeLines, plain, warn } from "./notices";
import { NOTHING_DRAWN } from "./render";

describe("the lines under the bar", () => {
  test("every source is said, warnings first, and a sentence twice is said once", () => {
    const notices = {
      document: [warn("Signed.")],
      export: plain("Downloaded contract.pdf."),
      reopen: plain("Opened again."), // said in the bar, not under it
      font: null,
    };
    const drawn = {
      ...NOTHING_DRAWN,
      notices: new Map([
        [0, [aSpanNotice("Drawn in Liberation Serif.")]],
        [3, [aSpanNotice("Drawn in Liberation Serif.")]],
      ]),
      skipped: [aSkipped(2, "An edit points at nothing.")],
      failed: "Couldn't reach the server.",
    };
    expect(noticeLines(notices, drawn)).toEqual([
      warn("Signed."),
      warn("Couldn't reach the server."),
      warn("Drawn in Liberation Serif."),
      warn("An edit points at nothing."),
      plain("Downloaded contract.pdf."),
    ]);
  });

  test("nothing to say is no lines", () => {
    expect(noticeLines(NO_NOTICES, NOTHING_DRAWN)).toEqual([]);
  });
});
