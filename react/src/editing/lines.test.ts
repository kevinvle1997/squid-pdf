import { expect, test } from "vitest";
import { aDoc, aSpan } from "../fixtures";
import { linesBySpan, startsALine } from "./lines";

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
