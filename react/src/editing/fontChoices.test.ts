import { describe, expect, test, vi } from "vitest";
import type { Render } from "../api/types";
import { A4, aDoc, aFont, aSpan } from "../fixtures";
import { HANDWRITING_STEP, NEW_TEXT_ASCENT_EM } from "./constants";
import { createEditor, type Editor } from "./editor";
import { chooseFont, chosenFontOf, fontChoicesOf, sameChoices } from "./fontChoices";
import { openNewTextBelow, openNewTextOnBarePage } from "./newText";
import { typeInto } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(() => new Promise<Render>(() => undefined)),
}));

// A page of two fonts, the serif's line first; new text opens under the sans's line, in its style.
const SERIF = aSpan({ id: "serif", font: "Times-Roman", size: 10, bbox: { x0: 72, y0: 100, x1: 200, y1: 112 } });
const SANS = aSpan({ id: "sans", font: "Helvetica", size: 10, bbox: { x0: 72, y0: 140, x1: 200, y1: 152 } });
const DOC = aDoc({ spans: [SERIF, SANS], fonts: [aFont("Times-Roman"), aFont("Helvetica")] });
const HANDWRITING = ["Caveat Regular", "Caveat Bold", "Great Vibes Regular"];

/** An editor on `doc`, the handwriting faces listed, with new text being typed. */
function typingNewText(doc = DOC): Editor {
  const editor = createEditor(new File(["%PDF-"], "a.pdf"), doc, 2);
  editor.store.set({ shippedFaces: { handwriting: HANDWRITING, failed: null } });
  if (doc.spans.length === 0) openNewTextOnBarePage(editor, 0);
  else openNewTextBelow(editor, SANS.id);
  typeInto(editor, "M. Halvorsen");
  return editor;
}

/** The new text being typed in `editor`. */
function draftIn(editor: Editor) {
  const { draft } = editor.store.get();
  if (draft?.kind !== "newText") throw new Error("new text is being typed");
  return draft;
}

describe("the fonts new text's slip offers", () => {
  test("are the page's own, in the order its text comes, then the handwriting faces we ship, by their names", () => {
    const editor = typingNewText();
    const { page, handwriting } = fontChoicesOf(editor.store.get(), draftIn(editor));
    expect(page.map((choice) => choice.label)).toEqual(["Times Roman", "Helvetica"]);
    expect(handwriting.map((choice) => choice.label)).toEqual(["Caveat", "Caveat Bold", "Great Vibes"]);
    // The one it opened in, the sans it's styled from, is the one chosen.
    expect(chosenFontOf(editor.store.get().layout, draftIn(editor))).toBe(page[1]?.key);
  });

  test("on a page with no text, offer the face new text opened in, so the one chosen is listed", () => {
    const editor = typingNewText(aDoc());
    const { page } = fontChoicesOf(editor.store.get(), draftIn(editor));
    expect(page.map((choice) => choice.label)).toEqual(["Liberation Sans"]);
    expect(chosenFontOf(editor.store.get().layout, draftIn(editor))).toBe(page[0]?.key);
  });

  test("are the same list when worked out again from the same state, and not once a face is added", () => {
    const editor = typingNewText();
    const once = fontChoicesOf(editor.store.get(), draftIn(editor));
    expect(sameChoices(once, fontChoicesOf(editor.store.get(), draftIn(editor)))).toBe(true);
    expect(sameChoices(once, { ...once, handwriting: once.handwriting.slice(1) })).toBe(false);
  });
});

describe("a font chosen from the slip", () => {
  test("a handwriting face sets new text a step larger than the text it's styled from; a page's font, at that text's size", () => {
    const editor = typingNewText();
    chooseFont(editor, "face:Caveat Bold", { widthOf: () => 100 });
    expect(draftIn(editor).style).toMatchObject({ font: "Caveat Bold", fontObject: null, size: 10 * HANDWRITING_STEP });
    const [serif] = fontChoicesOf(editor.store.get(), draftIn(editor)).page;
    chooseFont(editor, serif?.key ?? "", { widthOf: () => 100 });
    expect(draftIn(editor).style).toMatchObject({ font: "Times-Roman", size: 10 });
    expect(editor.store.get().announcement.text).toBe("Set in Times Roman.");
  });

  test("a step larger, new text moves in from the edges it would now run past", () => {
    const editor = typingNewText(aDoc());
    editor.store.set({ draft: { ...draftIn(editor), origin: [A4.width - 100, 12] } });

    chooseFont(editor, "face:Caveat Bold", { widthOf: () => 150 });
    const { style, origin } = draftIn(editor);
    expect(style.font).toBe("Caveat Bold");
    expect(origin).toEqual([A4.width - 150, style.size * NEW_TEXT_ASCENT_EM]);
  });
});
