import { describe, expect, test, vi } from "vitest";
import type { Render } from "../api/types";
import { A4, aDoc, aFit, aFont, anInsert, aSpan, halfWideGlyphs } from "../fixtures";
import { INSERT_ASCENT_EM, INSERT_DESCENT_EM, NEW_TEXT_SIZE_PT } from "./constants";
import { change, createEditor, layoutOf } from "./editor";
import { chooseFace, keptTexts, moveText, shownTextsAmong, writeAt, writeOnBarePage } from "./newText";
import { typeInto, typeIntoText } from "./typing";

vi.mock(import("../api/client"), async (original) => ({
  ...(await original()),
  renderStrips: vi.fn(() => new Promise<Render>(() => undefined)),
}));

// Arial is only named in the file: a similar font draws new text in it. Caveat Bold, a face we ship, draws its own.
const ARIAL = aSpan({
  id: "arial",
  font: "Arial",
  fidelity: "substitute",
  substitute: "Liberation Sans Regular",
  in_file: false,
});
const LAYOUT = layoutOf(
  aDoc({
    spans: [ARIAL],
    fonts: [aFont("Arial", { substitute: "Liberation Sans Regular", glyphs: halfWideGlyphs("ab") })],
  }),
);
const inArial = { id: 1, textId: 1, edit: anInsert({ font: "Arial", text: "ab" }) };
const inCaveat = { id: 2, textId: 2, edit: anInsert({ font: "Caveat Bold", text: "ab" }) };

describe("the new text a shown row of the bar's list marks", () => {
  test("is the new text in that row's font, not text its own face draws, and none while no row is shown", () => {
    const facts = { layout: LAYOUT, fits: {} };
    expect(shownTextsAmong([inArial, inCaveat], { ...facts, shown: new Set(["font:Arial"]) })).toEqual([inArial]);
    expect(shownTextsAmong([inArial, inCaveat], { ...facts, shown: new Set() })).toEqual([]);
  });

  test("goes by its render once that version landed", () => {
    const fits = { 1: aFit({ fidelity: "exact" }) };
    expect(shownTextsAmong([inArial], { layout: LAYOUT, fits, shown: new Set(["font:Arial"]) })).toEqual([]);
  });
});

describe("new text kept on its page", () => {
  /** An editor with one piece of new text, `size` points, its line starting at `origin`. */
  function withText(origin: [number, number], size = 10) {
    const editor = createEditor(new File(["%PDF-"], "a.pdf"), aDoc(), 2);
    change(editor, { kind: "add", edits: [anInsert({ origin, size })] });
    const [text] = keptTexts(editor.store.get().reading);
    if (text === undefined) throw new Error("the new text is on its page");
    return { editor, textId: text.textId };
  }
  const originOf = (editor: ReturnType<typeof withText>["editor"]) =>
    keptTexts(editor.store.get().reading)[0]?.edit.origin;

  test("dropped past the right edge, its whole line stays on: it ends at the edge, however wide it draws", () => {
    const { editor, textId } = withText([400, 300]);
    moveText(editor, textId, { by: [300, 0], nudge: false, widthPt: 120 });
    expect(originOf(editor)).toEqual([A4.width - 120, 300]);
  });

  test("nudged past the bottom, its letters' feet stay on; past the top, their heads", () => {
    const { editor, textId } = withText([72, A4.height - 20], 10);
    moveText(editor, textId, { by: [0, 20], nudge: true, widthPt: 40 });
    expect(originOf(editor)).toEqual([72, A4.height - 10 * INSERT_DESCENT_EM]);
    moveText(editor, textId, { by: [0, -A4.height], nudge: true, widthPt: 40 });
    expect(originOf(editor)).toEqual([72, 10 * INSERT_ASCENT_EM]);
  });

  test("set in a handwriting face a step larger, it moves in from the edge it would now run past", () => {
    const editor = createEditor(new File(["%PDF-"], "a.pdf"), aDoc(), 2);
    editor.store.set({ shippedFaces: { handwriting: ["Caveat Bold"], failed: null } });
    writeOnBarePage(editor, 0);
    typeInto(editor, "M. Halvorsen");
    const draft = editor.store.get().draft;
    if (draft?.kind !== "text") throw new Error("new text is being typed");
    editor.store.set({ draft: { ...draft, origin: [A4.width - 100, 12] } });

    chooseFace(editor, "face:Caveat Bold", { widthOf: () => 150 });
    const chosen = editor.store.get().draft;
    expect(chosen?.kind === "text" && chosen.style.font).toBe("Caveat Bold");
    const size = chosen?.kind === "text" ? chosen.style.size : 0;
    expect(chosen?.kind === "text" && chosen.origin).toEqual([A4.width - 150, size * INSERT_ASCENT_EM]);
  });

  /** An editor on a page with no text, its new text's field opened by a press at `at`. */
  function openedAt(at: [number, number]) {
    const editor = createEditor(new File(["%PDF-"], "a.pdf"), aDoc(), 2);
    writeAt(editor, 0, at);
    return editor;
  }
  const draftOriginOf = (editor: ReturnType<typeof openedAt>) => {
    const draft = editor.store.get().draft;
    return draft?.kind === "text" ? draft.origin : undefined;
  };

  test("opened by a press at the bottom edge, its baseline is on the page, its letters' feet with it", () => {
    const editor = openedAt([300, A4.height - 1]);
    expect(draftOriginOf(editor)).toEqual([300, A4.height - NEW_TEXT_SIZE_PT * INSERT_DESCENT_EM]);
  });

  test("typed past the room to its right, its start moves left so it ends at the edge, never past the left edge", () => {
    const editor = openedAt([560, 300]);
    const [, baseline] = draftOriginOf(editor) ?? [];
    typeIntoText(editor, "Signed", { widthOf: () => 20 });
    expect(draftOriginOf(editor)).toEqual([560, baseline]);
    typeIntoText(editor, "Signed by M. Halvorsen", { widthOf: () => 120 });
    expect(draftOriginOf(editor)).toEqual([A4.width - 120, baseline]);
    typeIntoText(editor, "Signed by M. Halvorsen, and more than a page holds", { widthOf: () => A4.width + 50 });
    expect(draftOriginOf(editor)).toEqual([0, baseline]);
  });
});
