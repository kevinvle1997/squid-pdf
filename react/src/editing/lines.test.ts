import { describe, expect, test } from "vitest";
import { aDoc, aSpan } from "../fixtures";
import { linesBySpan, runsEdited, runsRedrawn, runTypedInto, startsALine } from "./lines";

// "Text is grouped into", "spans" in bold, "(one style)": one line, and a heading on a line of its own.
const before = aSpan({ id: "before", text: "Text is grouped into ", bbox: { x0: 72, y0: 100, x1: 160, y1: 112 } });
const bold = aSpan({ id: "bold", text: "spans", font: "Times-Bold", bbox: { x0: 160, y0: 99, x1: 186, y1: 112 } });
const after = aSpan({ id: "after", text: " (one style)", bbox: { x0: 186, y0: 100, x1: 240, y1: 113 } });
const heading = aSpan({ id: "heading", text: "Spans", bbox: { x0: 72, y0: 60, x1: 120, y1: 76 } });
const doc = aDoc({
  spans: [heading, before, bold, after],
  lines: [{ span_ids: ["before", "bold", "after"], room_pt: 30 }],
});

test("each span of a line finds the one line, its spans left to right, the box they make and its room", () => {
  const lines = linesBySpan(doc);
  const line = lines.get("bold");
  expect(line).toEqual({
    id: "before",
    spans: [before, bold, after],
    bbox: { x0: 72, y0: 99, x1: 240, y1: 113 },
    roomPt: 30,
  });
  // One object, whichever span asks: a mark drawn from it is drawn again only when it changes.
  expect(lines.get("before")).toBe(line);
  expect(lines.get("after")).toBe(line);
});

test("a span on no line of more than one is on none listed, and starts its own", () => {
  const lines = linesBySpan(doc);
  expect(lines.has("heading")).toBe(false);
  expect(startsALine(lines, heading)).toBe(true);
  expect(startsALine(lines, before)).toBe(true);
  expect(startsALine(lines, bold)).toBe(false);
});

test("a line naming a span the document lacks is no line", () => {
  const lines = linesBySpan({ ...doc, lines: [{ span_ids: ["before", "gone"], room_pt: 0 }] });
  expect(lines.size).toBe(0);
});

describe("typing into a line's runs", () => {
  const runs = ["Text is grouped into ", "spans", " (one style)"];

  test("words typed go into the run they're typed in, the others as they were", () => {
    expect(runsEdited(runs, { start: 23, end: 23, typed: "AN" })).toEqual([
      "Text is grouped into ",
      "spANans",
      " (one style)",
    ]);
  });

  test("at the boundary between two runs, the one before takes them; at the line's start, the first", () => {
    // After "into ", before "spans": the regular run before, not the bold one.
    expect(runsEdited(runs, { start: 21, end: 21, typed: "x" })).toEqual([
      "Text is grouped into x",
      "spans",
      " (one style)",
    ]);
    // After "spans", before " (one": the bold one.
    expect(runsEdited(runs, { start: 26, end: 26, typed: "!" })).toEqual([
      "Text is grouped into ",
      "spans!",
      " (one style)",
    ]);
    expect(runsEdited(runs, { start: 0, end: 0, typed: "> " })).toEqual([
      "> Text is grouped into ",
      "spans",
      " (one style)",
    ]);
  });

  test("a range across runs comes out of each, and what replaces it goes in the run it starts in", () => {
    expect(runsEdited(runs, { start: 16, end: 30, typed: "by" })).toEqual(["Text is grouped by", "", "e style)"]);
  });

  test("an emptied run is gone: typing where it was goes in the run before", () => {
    const emptied = ["Text is grouped into ", "", " (one style)"];
    expect(runTypedInto(emptied, 21)).toBe(0);
    expect(runsEdited(emptied, { start: 21, end: 21, typed: "words" })).toEqual([
      "Text is grouped into words",
      "",
      " (one style)",
    ]);
  });

  test("with every run emptied, the line's first takes what's typed", () => {
    expect(runsEdited(["", "", ""], { start: 0, end: 0, typed: "new" })).toEqual(["new", "", ""]);
  });
});

test("an edit redraws its runs from the first changed to the last, on to the line's end if it moves the rest; untouched, any", () => {
  expect(runsRedrawn([false, true, false, true, false], { movesTheRest: false })).toEqual([
    false,
    true,
    true,
    true,
    false,
  ]);
  expect(runsRedrawn([false, true, false, false], { movesTheRest: true })).toEqual([false, true, true, true]);
  expect(runsRedrawn([false, false, false], { movesTheRest: true })).toEqual([true, true, true]);
});
