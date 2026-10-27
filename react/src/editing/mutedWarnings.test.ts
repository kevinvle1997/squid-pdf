import { expect, test } from "vitest";
import { aDoc, aFont, aSpan, PSEUDO_SENTENCES } from "../fixtures";
import { createEditor } from "./editor";
import { setWarningMuted } from "./mutedWarnings";

// Its sentences are the server's, in a language made up for tests, so any the browser writes itself stands out.
const DOC = aDoc({
  sentences: PSEUDO_SENTENCES,
  spans: [aSpan({ id: "similar", font: "Named", fidelity: "substitute", substitute: "Liberation Serif Regular" })],
  fonts: [aFont("Named", { substitute: "Liberation Serif Regular" })],
});

test("a font muted warns no more, and says so in the server's words; warned again, it's back, and says that", () => {
  const editor = createEditor(new File(["%PDF-"], "contract.pdf"), DOC, 2);
  setWarningMuted(editor, "Named", { muted: true });
  expect(editor.store.get().mutedFonts).toEqual(new Set(["Named"]));
  expect(editor.store.get().announcement.text).toBe("EDITS IN Named WON'T WARN AGAIN.");
  setWarningMuted(editor, "Named", { muted: false });
  expect(editor.store.get().mutedFonts).toEqual(new Set());
  expect(editor.store.get().announcement.text).toBe("EDITS IN Named WARN AGAIN.");
});
