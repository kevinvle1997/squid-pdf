// The fonts new text can be set in, as its slip lists them: the page's own, then the handwriting faces we ship.
import type { Insert, SpanInfo } from "../api/types";
import { HANDWRITING_STEP, NEW_TEXT_FACE } from "./constants";
import { announcing, type Editor, type EditorState, type Layout } from "./editor";
import { readableName } from "./fonts";
import {
  inShippedFace,
  insertOf,
  keptOnPage,
  type NewTextDraft,
  type NewTextStyle,
  newTextFaceOf,
  styleOf,
} from "./newText";
import { shownNameIn } from "./spanFonts";
import { fill } from "./words";

/** One face the slip offers new text: a font of the page's, by its shown name, or a face we ship. */
export interface FontChoice {
  readonly key: string;
  readonly label: string; // its name, as the reader knows it
  readonly face: string; // the face its name is set in
  readonly handwriting: boolean;
}

/** The fonts the slip offers, the page's and the handwriting faces we ship. */
export interface FontChoices {
  readonly page: readonly FontChoice[];
  readonly handwriting: readonly FontChoice[];
}

/** The faces the slip offers: the page's own fonts, else the face it opened in; then the handwriting faces we ship. */
export function fontChoicesOf(state: Pick<EditorState, "layout" | "shippedFaces">, draft: NewTextDraft): FontChoices {
  const { layout, shippedFaces } = state;
  const handwriting = shippedFaces.handwriting.map((name) => shippedChoice(name, true));
  const page = [...pageFonts(layout, draft)].map(([shown, span]) => {
    const insert = { ...insertOf({ ...draft, style: styleOf(span) }), text: readableName(shown) };
    return { key: pageKey(shown), label: readableName(shown), face: newTextFaceOf(layout, insert), handwriting: false };
  });
  // A page with no text of its own offers the face new text opened in, so the one chosen is listed.
  if (page.length === 0) return { page: [shippedChoice(NEW_TEXT_FACE, false)], handwriting };
  return { page, handwriting };
}

/** The page's fonts by their shown names, in the order their text comes, each with a span set in it: the nearest's, for its own. */
function pageFonts(layout: Layout, draft: NewTextDraft): Map<string, SpanInfo> {
  const near = draft.nearId === null ? undefined : layout.spans.get(draft.nearId);
  const spans = (layout.pages.get(draft.page) ?? []).filter((span) => !span.form_field);
  const fonts = new Map<string, SpanInfo>();
  // The first span of a font stands for it, but for the one the text is styled from.
  for (const span of near === undefined ? spans : [near, ...spans]) {
    const shown = shownNameIn(layout.fonts, span);
    if (!fonts.has(shown)) fonts.set(shown, span);
  }
  return new Map([...fonts].sort(([, a], [, b]) => spans.indexOf(a) - spans.indexOf(b)));
}

const pageKey = (shown: string) => `page:${shown}`;
const shippedKey = (face: string) => `face:${face}`;

/** A face we ship, as the slip lists it. */
function shippedChoice(face: string, handwriting: boolean): FontChoice {
  return { key: shippedKey(face), label: shippedNameOf(face), face, handwriting };
}

/** A face we ship by the name the slip gives it: its own, "Regular" aside. */
export function shippedNameOf(face: string): string {
  return face.replace(/ Regular$/, "");
}

/** The face the slip shows chosen for `draft`. */
export function chosenFontOf(layout: Pick<Layout, "fonts">, draft: NewTextDraft): string {
  const { font, fontObject } = draft.style;
  if (inShippedFace({ font, font_object: fontObject })) return shippedKey(font);
  return pageKey(shownNameIn(layout.fonts, { font, font_object: fontObject }));
}

/** The style `draft` takes in the face `key` names: a handwriting face a step larger than the text it's styled from. */
function styleIn(
  state: Pick<EditorState, "layout" | "shippedFaces">,
  draft: NewTextDraft,
  key: string,
): NewTextStyle | null {
  const { nearSize, color } = draft.style;
  const choices = fontChoicesOf(state, draft);
  const chosen = [...choices.page, ...choices.handwriting].find((choice) => choice.key === key);
  if (chosen === undefined) return null;
  if (key.startsWith("face:")) {
    const size = chosen.handwriting ? nearSize * HANDWRITING_STEP : nearSize;
    return { font: chosen.face, fontObject: null, size, nearSize, color };
  }
  const span = pageFonts(state.layout, draft).get(key.slice("page:".length));
  return span === undefined ? null : { ...styleOf(span), size: nearSize, nearSize, color };
}

/**
 * Set the new text being typed in the face `key` names, from the slip's list: drawn in it at once, and announced.
 * Larger in a handwriting face, it stays on its page as wide as `widthOf` measures it.
 */
export function chooseFont(editor: Editor, key: string, { widthOf }: { widthOf: (insert: Insert) => number }): void {
  const state = editor.store.get();
  const { draft, doc } = state;
  const info = draft === null ? undefined : doc.pages[draft.page];
  if (draft?.kind !== "newText" || info === undefined) return;
  const style = styleIn(state, draft, key);
  if (style === null) return;
  const label = [...Object.values(fontChoicesOf(state, draft)).flat()].find((choice) => choice.key === key)?.label;
  const origin = keptOnPage(info, { origin: draft.origin, size: style.size }, widthOf(insertOf({ ...draft, style })));
  const setIn = fill(doc.sentences.new_text_set_in, { font: label ?? style.font });
  editor.store.set({ draft: { ...draft, style, origin }, announcement: announcing(setIn) });
}

/** Whether two lists of fonts offer the same, by key and face: each is worked out afresh from the state. */
export function sameChoices(a: FontChoices, b: FontChoices): boolean {
  const same = (x: readonly FontChoice[], y: readonly FontChoice[]) =>
    x.length === y.length && x.every((choice, index) => choice.key === y[index]?.key && choice.face === y[index]?.face);
  return same(a.page, b.page) && same(a.handwriting, b.handwriting);
}
