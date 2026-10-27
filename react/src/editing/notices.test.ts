import { describe, expect, test } from "vitest";
import { aSkipped, aSpanNotice } from "../fixtures";
import { NO_NOTICES, noticeLines, noticesAfterChange, plain, warn } from "./notices";
import { UNEDITED } from "./project";
import { NOTHING_DRAWN } from "./render";

const SIGNED = { ...warn("Signed."), type: "signed_file" };

describe("the lines under the bar", () => {
  test("every source is said, warnings first, and a sentence twice is said once", () => {
    const notices = {
      document: [SIGNED],
      export: [plain("Downloaded contract.pdf.")],
      reopen: plain("Opened again."), // said in the bar, not under it
      font: null,
      drop: null,
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
    expect(noticeLines(notices, drawn, UNEDITED)).toEqual([
      SIGNED,
      warn("Couldn't reach the server."),
      warn("Drawn in Liberation Serif."),
      warn("An edit points at nothing."),
      plain("Downloaded contract.pdf."),
    ]);
  });

  test("nothing to say is no lines", () => {
    expect(noticeLines(NO_NOTICES, NOTHING_DRAWN, UNEDITED)).toEqual([]);
  });

  test("a change to the history clears every line but what opening found", () => {
    const notices = {
      document: [SIGNED],
      export: [plain("Downloaded contract.pdf.")],
      reopen: plain("Opened again."),
      font: { ...warn("Not this font."), font: "Times" },
      drop: warn("Not a PDF."),
    };
    expect(noticesAfterChange(notices, { stale: false })).toEqual({ ...NO_NOTICES, document: [SIGNED] });
    // Why a stale reading couldn't be read again stays, until it is.
    const stale = noticesAfterChange(notices, { stale: true });
    expect(stale).toEqual({ ...NO_NOTICES, document: [SIGNED], reopen: plain("Opened again.") });
  });
});
