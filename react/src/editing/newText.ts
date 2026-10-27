// New text on a page: where a press or a key puts it, the text it takes its style from, the faces it can be set
// in, how it comes out (Rule 1, as an edit of a span does), and moving it. A typed signature is new text in a
// handwriting face, drawn a step larger: no kind of its own.
import type { FitInfo, FontInfo, Insert, PageInfo, Sentences, SpanInfo } from "../api/types";
import {
  HANDWRITING_STEP,
  INSERT_ASCENT_EM,
  INSERT_DESCENT_EM,
  LINE_BELOW_EM,
  NEW_TEXT_FACE,
  NEW_TEXT_MARGIN_PT,
  NEW_TEXT_SIZE_PT,
} from "./constants";
import { announcing, change, type Editor, type EditorState, type FocusTo, type Layout } from "./editor";
import { DEFAULT_FACE, isShippedFace, previewFaceOf } from "./faces";
import { letterTroublesOf, missingIn } from "./fit";
import { readableName } from "./fonts";
import { versionsOf } from "./history";
import type { InsertReading, Reading } from "./project";
import type { Drawn } from "./render";
import { landedInsertFitOf } from "./render";
import { fontOf, shownNameIn } from "./spanFonts";
import { type Drawing, drawingIn, drawingOf } from "./substitutes";
import { EXACT, liveVerdictOf, type Verdict } from "./verdicts";
import { fill } from "./words";

/** A point on a page, in its own points from its top left. */
export type PagePoint = readonly [x: number, y: number];

type Color = NonNullable<Insert["color"]>;
const BLACK: Color = [0, 0, 0];

/** What new text is set in, as the insert sends it. */
export interface TextStyle {
  readonly font: string; // a page font's name, as its text reads it, or a face we ship
  readonly fontObject: number | null; // the page font's object, from the span the style came from
  readonly size: number;
  readonly nearSize: number; // the size of the text it's styled from: a handwriting face is a step larger
  readonly color: Color;
}

/** New text being typed, not yet in the history: on a page, at its baseline's start. */
export interface TextDraft {
  readonly kind: "text";
  readonly page: number;
  readonly of: InsertReading | null; // the text typed into again, as it reads now; none for new text
  readonly origin: PagePoint;
  readonly text: string;
  readonly style: TextStyle;
  readonly nearId: string | null; // the span its style came from, whose font the face list names first
  readonly back: FocusTo | null; // where focus goes when it's put away with Escape
}

/** The text a press at `at` takes its style from: the line above it, else the closest. A form field's is its field's. */
export function nearestSpan(spans: readonly SpanInfo[], [x, y]: PagePoint): SpanInfo | undefined {
  const lines = spans.filter((span) => !span.form_field);
  const across = (span: SpanInfo) => Math.max(span.bbox.x0 - x, 0, x - span.bbox.x1);
  const down = (span: SpanInfo) => Math.max(span.bbox.y0 - y, 0, y - span.bbox.y1);
  const above = lines.filter((span) => span.bbox.y1 <= y);
  // Above: the nearest line up, and of a row of them, the one over the press.
  if (above.length > 0) return closest(above, (span) => y - span.bbox.y1 + across(span));
  return closest(lines, (span) => Math.hypot(across(span), down(span)));
}

/** The one of `spans` `distance` puts nearest; the first of those as near. */
function closest(spans: readonly SpanInfo[], distance: (span: SpanInfo) => number): SpanInfo | undefined {
  return spans.reduce<SpanInfo | undefined>(
    (best, span) => (best === undefined || distance(span) < distance(best) ? span : best),
    undefined,
  );
}

/** The style of `span`'s text, for new text near it; with none, a plain face we ship (`NEW_TEXT_FACE`). */
export function styleOf(span: SpanInfo | undefined): TextStyle {
  if (span === undefined) {
    return { font: NEW_TEXT_FACE, fontObject: null, size: NEW_TEXT_SIZE_PT, nearSize: NEW_TEXT_SIZE_PT, color: BLACK };
  }
  const [red = 0, green = 0, blue = 0] = span.color;
  const color: Color = [red, green, blue];
  return { font: span.font, fontObject: span.font_object, size: span.size, nearSize: span.size, color };
}

/** Where a line of `size` starts so its middle sits at `at`, as a press there asks: its box, an em up and a little down. */
export function originAt([x, y]: PagePoint, size: number): PagePoint {
  return [x, y + (size * (INSERT_ASCENT_EM - INSERT_DESCENT_EM)) / 2];
}

/** The insert `draft` sends as it is now. */
export function insertOf({ page, origin, text, style }: Pick<TextDraft, "page" | "origin" | "text" | "style">): Insert {
  const [x, y] = origin;
  const named = style.fontObject === null ? {} : { font_object: style.fontObject };
  return {
    kind: "insert",
    page,
    origin: [x, y],
    text,
    size: style.size,
    font: style.font,
    color: style.color,
    ...named,
  };
}

/** Whether two inserts draw the same: the same words, place and style. */
export function sameInsert(a: Insert, b: Insert): boolean {
  const same = (x: readonly number[] | undefined, y: readonly number[] | undefined) =>
    (x ?? []).length === (y ?? []).length && (x ?? []).every((value, index) => value === y?.[index]);
  return (
    a.page === b.page &&
    a.text === b.text &&
    a.size === b.size &&
    a.font === b.font &&
    (a.font_object ?? null) === (b.font_object ?? null) &&
    same(a.origin, b.origin) &&
    same(a.color, b.color)
  );
}

/** The font new text names: none named is the server's own default, the face `NEW_TEXT_FACE` names. */
export function fontNameOf(insert: Pick<Insert, "font">): string {
  return insert.font ?? NEW_TEXT_FACE;
}

/** Whether new text is set in a face we ship, not one of the page's fonts. */
export function inShippedFace(insert: Pick<Insert, "font" | "font_object">): boolean {
  return (insert.font_object ?? null) === null && isShippedFace(fontNameOf(insert));
}

/** The page's font new text is in, as the document lists it; none in a face we ship. */
export function fontOfText(layout: Pick<Layout, "fonts">, insert: Insert): FontInfo | undefined {
  if (inShippedFace(insert)) return undefined;
  return fontOf(layout.fonts, { font: fontNameOf(insert), font_object: insert.font_object ?? null });
}

/**
 * New text in a page's font as a span of it, so it's judged and previewed as an edit of that font is: drawn level
 * and unspaced, as the server draws new text, and by the file's own copy where the font's spans say it can.
 */
export function asSpanOf(insert: Insert, { font, inFile }: { font: FontInfo | undefined; inFile: boolean }): SpanInfo {
  const [x, y] = insert.origin;
  const { size } = insert;
  return {
    id: "",
    page: insert.page,
    text: insert.text,
    font: fontNameOf(insert),
    font_object: insert.font_object ?? null,
    size,
    color: [...(insert.color ?? BLACK)],
    bbox: { x0: x, y0: y - size * INSERT_ASCENT_EM, x1: x, y1: y + size * INSERT_DESCENT_EM },
    origin: [x, y],
    fidelity: "exact",
    why: null,
    substitute: font?.substitute ?? null,
    same_widths: font?.same_widths ?? false,
    in_file: inFile,
    letter_spacing_em: 0,
    room_pt: 0,
    form_field: false,
  };
}

/** New text as the span its page's font judges it as: the font's own copy draws it where any span of that font says so. */
export function textSpanOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): SpanInfo {
  const font = fontOfText(layout, insert);
  const key = insert.font_object ?? fontNameOf(insert);
  const inFont = [...layout.spans.values()].find((span) => (span.font_object ?? span.font) === key);
  return asSpanOf(insert, { font, inFile: inFont?.in_file ?? font?.substitute == null });
}

/**
 * How new text comes out, the one verdict its note, the chip and the bar say: its render's once that very version
 * landed (`fit`); in a face we ship, as asked, since only a letter it lacks sends it elsewhere, and its troubles say
 * that; in a page's font, as an edit of that font is worked out.
 */
export function textVerdictOf(
  layout: Pick<Layout, "fonts" | "spans">,
  { insert, fit }: { insert: Insert; fit: FitInfo | undefined },
): Verdict {
  if (fit !== undefined) return fit;
  if (inShippedFace(insert)) return EXACT;
  return liveVerdictOf(textSpanOf(layout, insert), { font: fontOfText(layout, insert), text: insert.text });
}

/** How new text is drawn: in what face, and the letters that send it there or are left out. */
export function textDrawingOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): Drawing {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text);
  const font = fontOfText(layout, insert);
  if (font === undefined) return { face: DEFAULT_FACE, switched: [], leftOut: [] };
  const span = textSpanOf(layout, insert);
  return drawingOf(span, { font, text: insert.text, missing: missingIn(insert.text, font.glyphs) });
}

/** The face new text previews in: a face we ship is the very file; a page font's, as an edit of it previews. */
export function textFaceOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): string {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text).face;
  const font = fontOfText(layout, insert);
  return font === undefined ? DEFAULT_FACE : previewFaceOf(textSpanOf(layout, insert), { font, text: insert.text });
}

/** What won't come out of new text as typed, in the server's words: only letters, as it has no width to fit. */
export function textTroublesOf(
  layout: Pick<Layout, "fonts" | "spans">,
  insert: Insert,
  sentences: Sentences,
): string[] {
  return letterTroublesOf(textDrawingOf(layout, insert), sentences);
}

/** New text at rest, as its verdict says: the render's for its latest version once that landed, else worked out here. */
export function restingVerdictOf(state: Pick<EditorState, "layout" | "drawn">, text: InsertReading): Verdict {
  return textVerdictOf(state.layout, { insert: text.edit, fit: landedInsertFitOf(state.drawn, text) });
}

/** The pieces of new text on the pages the download keeps. */
export function keptTexts(reading: Reading): InsertReading[] {
  return [...reading.pages].flatMap(([page, edits]) => (reading.deleted.has(page) ? [] : edits.inserts));
}

/** One face the slip offers new text: a font of the page's, by its shown name, or a face we ship. */
export interface FaceChoice {
  readonly key: string;
  readonly label: string; // its name, as the reader knows it
  readonly face: string; // the face its name is set in
  readonly handwriting: boolean;
}

/** The faces the slip offers: the page's own fonts, else the face it opened in; then the handwriting faces we ship. */
export function faceChoicesOf(
  state: Pick<EditorState, "layout" | "shippedFaces">,
  draft: TextDraft,
): { page: FaceChoice[]; handwriting: FaceChoice[] } {
  const { layout, shippedFaces } = state;
  const handwriting = shippedFaces.handwriting.map((name) => shippedChoice(name, true));
  const page = [...pageFonts(layout, draft)].map(([shown, span]) => {
    const insert = { ...insertOf({ ...draft, style: styleOf(span) }), text: readableName(shown) };
    return { key: pageKey(shown), label: readableName(shown), face: textFaceOf(layout, insert), handwriting: false };
  });
  // A page with no text of its own offers the face new text opened in, so the one chosen is listed.
  if (page.length === 0) return { page: [shippedChoice(NEW_TEXT_FACE, false)], handwriting };
  return { page, handwriting };
}

/** The page's fonts by their shown names, in the order their text comes, each with a span set in it: the nearest's, for its own. */
function pageFonts(layout: Layout, draft: TextDraft): Map<string, SpanInfo> {
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

/** A face we ship, as the slip lists it: by its name, "Regular" aside. */
function shippedChoice(face: string, handwriting: boolean): FaceChoice {
  return { key: shippedKey(face), label: face.replace(/ Regular$/, ""), face, handwriting };
}

/** The face the slip shows chosen for `draft`. */
export function chosenFaceOf(layout: Pick<Layout, "fonts">, draft: TextDraft): string {
  const { font, fontObject } = draft.style;
  if (inShippedFace({ font, font_object: fontObject })) return shippedKey(font);
  return pageKey(shownNameIn(layout.fonts, { font, font_object: fontObject }));
}

/** The style `draft` takes in the face `key` names: a handwriting face a step larger than the text it's styled from. */
function styleIn(state: Pick<EditorState, "layout" | "shippedFaces">, draft: TextDraft, key: string): TextStyle | null {
  const { nearSize, color } = draft.style;
  const choices = faceChoicesOf(state, draft);
  const chosen = [...choices.page, ...choices.handwriting].find((choice) => choice.key === key);
  if (chosen === undefined) return null;
  if (key.startsWith("face:")) {
    const size = chosen.handwriting ? nearSize * HANDWRITING_STEP : nearSize;
    return { font: chosen.face, fontObject: null, size, nearSize, color };
  }
  const span = pageFonts(state.layout, draft).get(key.slice("page:".length));
  return span === undefined ? null : { ...styleOf(span), size: nearSize, nearSize, color };
}

/** Set the new text being typed in the face `key` names, from the slip's list: drawn in it at once, and announced. */
export function chooseFace(editor: Editor, key: string): void {
  const state = editor.store.get();
  const { draft } = state;
  if (draft?.kind !== "text") return;
  const style = styleIn(state, draft, key);
  if (style === null) return;
  const label = [...Object.values(faceChoicesOf(state, draft)).flat()].find((choice) => choice.key === key)?.label;
  const setIn = fill(state.doc.sentences.new_text_set_in, { font: label ?? style.font });
  editor.store.set({ draft: { ...draft, style }, announcement: announcing(setIn) });
}

/** Open new text where `at` was pressed on bare paper, styled as the text nearest it. */
export function writeAt(editor: Editor, page: number, at: PagePoint): void {
  const { layout } = editor.store.get();
  const near = nearestSpan(layout.pages.get(page) ?? [], at);
  const style = styleOf(near);
  const origin = originAt(at, style.size);
  const back = { page, block: "nearest" } as const;
  open(editor, { kind: "text", page, of: null, origin, text: "", style, nearId: near?.id ?? null, back });
}

/** Open new text on the line below a span, at its left edge, in its style; focus comes back to it with Escape. */
export function writeBelow(editor: Editor, spanId: string): void {
  const span = editor.store.get().layout.spans.get(spanId);
  if (span === undefined) return;
  const [, baseline] = span.origin;
  const origin: PagePoint = [span.bbox.x0, (baseline ?? span.bbox.y1) + span.size * LINE_BELOW_EM];
  const draft = { page: span.page, of: null, origin, text: "", style: styleOf(span), nearId: span.id };
  open(editor, { kind: "text", ...draft, back: { spanId } });
}

/** Open new text at the top left of a page with no text, inside its margin. */
export function writeOnBarePage(editor: Editor, page: number): void {
  const style = styleOf(undefined);
  const origin: PagePoint = [NEW_TEXT_MARGIN_PT, NEW_TEXT_MARGIN_PT + style.size];
  open(editor, {
    kind: "text",
    page,
    of: null,
    origin,
    text: "",
    style,
    nearId: null,
    back: { page, block: "nearest" },
  });
}

/** Type into new text again, from its mark: Enter, or a click. */
export function writeAgain(editor: Editor, textId: number): void {
  const { reading, layout, shippedFaces } = editor.store.get();
  const text = keptTexts(reading).find((insert) => insert.textId === textId);
  if (text === undefined) return;
  const { page, origin, font_object: fontObject = null, size, color = BLACK } = text.edit;
  const font = fontNameOf(text.edit);
  const handwriting = inShippedFace(text.edit) && shippedFaces.handwriting.includes(font);
  const nearSize = handwriting ? size / HANDWRITING_STEP : size;
  const [x, y] = origin;
  const near = nearestSpan(layout.pages.get(page) ?? [], [x, y]);
  const style = { font, fontObject, size, nearSize, color };
  open(editor, {
    kind: "text",
    page,
    of: text,
    origin,
    text: text.edit.text,
    style,
    nearId: near?.id ?? null,
    back: { page, block: "nearest" },
  });
}

function open(editor: Editor, draft: TextDraft): void {
  editor.store.set({ draft, focusTo: null, unredacting: null });
}

/** Move new text by `by` points: a drag, a step of its own; a nudge, one with the nudges just before it. */
export function moveText(editor: Editor, textId: number, { by, nudge }: { by: PagePoint; nudge: boolean }): void {
  const { reading, doc, nudged } = editor.store.get();
  const text = keptTexts(reading).find((insert) => insert.textId === textId);
  const info = text === undefined ? undefined : doc.pages[text.edit.page];
  if (text === undefined || info === undefined) return;
  const origin = within(info, text.edit, by);
  const edit = { ...text.edit, origin: [origin[0], origin[1]] as [number, number] };
  const folds = nudge && nudged === text.id;
  const next = editor.store.get().history.next;
  change(
    editor,
    { kind: "revise", replaces: text.id, edit, folds },
    {
      nudged: nudge ? next : null,
      focusTo: { textId },
      announcement: announcing(fill(doc.sentences.new_text_moved, { text: text.edit.text })),
    },
  );
}

/** Where new text moved `by` starts, kept on its page: a line moved off it would draw nowhere. */
function within(info: PageInfo, { origin: [x, y], size }: Insert, [dx, dy]: PagePoint): PagePoint {
  const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);
  return [clamp(x + dx, 0, info.width - size), clamp(y + dy, size * INSERT_ASCENT_EM, info.height)];
}

/** Take new text out, every version of it, from its margin note: focus goes to its page's own control. */
export function takeOut(editor: Editor, textId: number): void {
  const { history, reading, doc } = editor.store.get();
  const text = keptTexts(reading).find((insert) => insert.textId === textId);
  if (text === undefined) return;
  const focusTo = { page: text.edit.page, block: "nearest" } as const;
  change(
    editor,
    { kind: "remove", ids: versionsOf(history, textId) },
    { announcement: announcing(fill(doc.sentences.new_text_taken_out, { text: text.edit.text })), focusTo },
  );
}

/** A press at `client` on a page's layer, drawn in `shown` on screen, as a point on the page in its own points. */
export function pointOnPage(
  [clientX, clientY]: PagePoint,
  { shown, info }: { shown: { left: number; top: number; width: number; height: number }; info: PageInfo },
): PagePoint {
  const [dx, dy] = unturned([clientX - (shown.left + shown.width / 2), clientY - (shown.top + shown.height / 2)], info);
  const pxPerPt = (info.turn_cw % 180 === 0 ? shown.width : shown.height) / info.width;
  return [info.width / 2 + dx / pxPerPt, info.height / 2 + dy / pxPerPt];
}

/** A move of `by` pixels on screen as one on the page, shown `shownWidthPx` across its own width: turned back as the page is. */
export function moveOnPage(by: PagePoint, { shownWidthPx, info }: { shownWidthPx: number; info: PageInfo }): PagePoint {
  const [dx, dy] = unturned(by, info);
  const pxPerPt = shownWidthPx / info.width;
  return pxPerPt > 0 ? [dx / pxPerPt, dy / pxPerPt] : [0, 0];
}

/** A move on screen as one across the page unturned: the page turns clockwise by `turn_cw`, so this turns back. */
function unturned([dx, dy]: PagePoint, info: PageInfo): PagePoint {
  const angle = (-info.turn_cw * Math.PI) / 180;
  const [cos, sin] = [Math.round(Math.cos(angle)), Math.round(Math.sin(angle))];
  return [dx * cos - dy * sin, dx * sin + dy * cos];
}

/** Whether the latest render of new text is of its words now: its strips show it, so nothing previews it. */
export function drawnAs(drawn: Drawn, text: InsertReading): boolean {
  return (
    drawn.from
      .get(text.edit.page)
      ?.pages.get(text.edit.page)
      ?.inserts.some((was) => was.id === text.id) ?? false
  );
}
