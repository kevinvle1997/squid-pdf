import { expect, test } from "vitest";
import { aSpan } from "../fixtures";
import { notePlaces } from "./margin";
import type { SpanReading } from "./project";

const changeAt = (id: string, y0: number): SpanReading => ({
  span: aSpan({ id, bbox: { x0: 72, y0, x1: 200, y1: y0 + 10 } }),
  text: "now",
  replaced: true,
  strategy: undefined,
  redacted: false,
});

test("a note sits level with its line, and one that would overlap the note above moves down", () => {
  // Given out of order: the places come back in the order given.
  const changes = [changeAt("low", 300), changeAt("top", 100), changeAt("close", 104)];
  expect(notePlaces(changes, 18)).toEqual([305, 105, 123]);
});
