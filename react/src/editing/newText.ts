// New text, a typed signature among it: where it opens, its style, how it comes out (Rule 1), and moving it.
import type { FitInfo, FontInfo, Insert, PageInfo, SpanInfo } from "../api/types";
import {
  HANDWRITING_STEP,
  LINE_BELOW_EM,
  NEW_TEXT_ASCENT_EM,
  NEW_TEXT_DESCENT_EM,
  NEW_TEXT_FACE,
  NEW_TEXT_MARGIN_PT,
  NEW_TEXT_SIZE_PT,
} from "./constants";
import { announcing, change, type Editor, type FocusTo, type Layout } from "./editor";
import { DEFAULT_FACE, isShippedFace, previewFaceOf } from "./faces";
import { missingIn } from "./fit";
import { versionsOf } from "./history";
import { type MismatchPlace, mismatchKeyOf } from "./mismatches";
import { pageControlOf } from "./pageList";
import type { NewTextReading, Reading } from "./project";
import type { PageNewTextFits } from "./render";
import { fontOf } from "./spanFonts";
import { type Drawing, drawingIn, drawingOf } from "./substitutes";
import { EXACT, liveVerdictOf, type Verdict } from "./verdicts";
import { fill } from "./words";

/** A point on a page, in its own points from its top left. */
export type PagePoint = readonly [x: number, y: number];

type Color = NonNullable<Insert["color"]>;
const BLACK: Color = [0, 0, 0];

/** What new text is set in, as the insert sends it. */
export interface NewTextStyle {
  readonly font: string; // a page font's name, as its text reads it, or a face we ship
  readonly fontObject: number | null; // the page font's object, from the span the style came from
  readonly size: number;
  readonly nearSize: number; // the size of the text it's styled from: a handwriting face is a step larger
  readonly color: Color;
}

/** New text being typed, not yet in the history: on a page, at its baseline's start. */
export interface NewTextDraft {
  readonly kind: "newText";
  readonly page: number;
  readonly of: NewTextReading | null; // the text typed into again, as it reads now; none for new text
  readonly origin: PagePoint;
  readonly text: string;
  readonly style: NewTextStyle;
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
export function styleOf(span: SpanInfo | undefined): NewTextStyle {
  if (span === undefined) {
    return { font: NEW_TEXT_FACE, fontObject: null, size: NEW_TEXT_SIZE_PT, nearSize: NEW_TEXT_SIZE_PT, color: BLACK };
  }
  const [red = 0, green = 0, blue = 0] = span.color;
  const color: Color = [red, green, blue];
  return { font: span.font, fontObject: span.font_object, size: span.size, nearSize: span.size, color };
}

/** Where a line of `size` starts so its middle sits at `at`, as a press there asks: its box, an em up and a little down. */
export function originAt([x, y]: PagePoint, size: number): PagePoint {
  return [x, y + (size * (NEW_TEXT_ASCENT_EM - NEW_TEXT_DESCENT_EM)) / 2];
}

/** The insert `draft` sends as it is now. */
export function insertOf({
  page,
  origin,
  text,
  style,
}: Pick<NewTextDraft, "page" | "origin" | "text" | "style">): Insert {
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

/** Whether two pieces of new text draw the same: the same words, place and style. */
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
export function fontOfNewText(layout: Pick<Layout, "fonts">, insert: Insert): FontInfo | undefined {
  if (inShippedFace(insert)) return undefined;
  return fontOf(layout.fonts, { font: fontNameOf(insert), font_object: insert.font_object ?? null });
}

/** Where new text's line sits, from its baseline's start, before it's measured: an em above it and a little below. */
export function newTextLineOf({ origin: [x, y], size }: { origin: PagePoint; size: number }): Box {
  return { x0: x, y0: y - size * NEW_TEXT_ASCENT_EM, x1: x, y1: y + size * NEW_TEXT_DESCENT_EM };
}

/** A box on a page, in its own points from its top left. */
export type Box = { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };

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
    bbox: newTextLineOf(insert),
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
export function newTextSpanOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): SpanInfo {
  const font = fontOfNewText(layout, insert);
  const key = insert.font_object ?? fontNameOf(insert);
  const inFont = [...layout.spans.values()].find((span) => (span.font_object ?? span.font) === key);
  return asSpanOf(insert, { font, inFile: inFont?.in_file ?? font?.substitute == null });
}

/**
 * How new text comes out, the one verdict its note, the chip and the bar say: its render's once that very version
 * landed (`fit`); in a face we ship, exact where that face draws it, else the face a letter it lacks sends the line
 * to; in a page's font, as an edit of that font is worked out.
 */
export function newTextVerdictOf(
  layout: Pick<Layout, "fonts" | "spans">,
  { insert, fit }: { insert: Insert; fit: FitInfo | undefined },
): Verdict {
  if (fit !== undefined) return fit;
  if (inShippedFace(insert)) return shippedVerdictOf(insert);
  return liveVerdictOf(newTextSpanOf(layout, insert), { font: fontOfNewText(layout, insert), text: insert.text });
}

/** New text in a face we ship: exact where it draws the line, else in the similar face a letter it lacks sends it to. */
function shippedVerdictOf(insert: Insert): Verdict {
  const { face } = drawingIn(fontNameOf(insert), insert.text);
  if (face === fontNameOf(insert)) return EXACT;
  return { fidelity: "substitute", why: null, substitute: face, same_widths: false };
}

/** How new text is drawn: in what face, and the letters that send it there or are left out. */
export function newTextDrawingOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): Drawing {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text);
  const font = fontOfNewText(layout, insert);
  if (font === undefined) return { face: DEFAULT_FACE, switched: [], leftOut: [] };
  const span = newTextSpanOf(layout, insert);
  return drawingOf(span, { font, text: insert.text, missing: missingIn(insert.text, font.glyphs) });
}

/** The face new text previews in: a face we ship is the very file; a page font's, as an edit of it previews. */
export function newTextFaceOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): string {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text).face;
  const font = fontOfNewText(layout, insert);
  return font === undefined ? DEFAULT_FACE : previewFaceOf(newTextSpanOf(layout, insert), { font, text: insert.text });
}

/** New text as a place an edit won't match, as the bar counts places: none where it will. */
export function newTextPlaceOf(
  layout: Pick<Layout, "fonts" | "spans">,
  { text, fit }: { text: NewTextReading; fit: FitInfo | undefined },
): MismatchPlace | undefined {
  const verdict = newTextVerdictOf(layout, { insert: text.edit, fit });
  if (verdict.fidelity === "exact") return undefined;
  const missing = fit?.missing ?? newTextDrawingOf(layout, text.edit).switched;
  return { span: newTextSpanOf(layout, text.edit), verdict, missing };
}

/** The new text of `texts` in a row of the bar's list that's shown, as the list counts places now. */
export function shownNewTextsAmong(
  texts: readonly NewTextReading[],
  {
    shown,
    layout,
    fits,
  }: { shown: ReadonlySet<string>; layout: Pick<Layout, "fonts" | "spans">; fits: PageNewTextFits },
): NewTextReading[] {
  return texts.filter((text) => {
    const place = newTextPlaceOf(layout, { text, fit: fits[text.id] });
    return place !== undefined && shown.has(mismatchKeyOf(place, layout.fonts));
  });
}

/** The pieces of new text on the pages the download keeps. */
export function keptNewTexts(reading: Reading): NewTextReading[] {
  return [...reading.pages].flatMap(([page, edits]) => (reading.deleted.has(page) ? [] : edits.newTexts));
}

/** Where focus goes when new text at `insert`'s place goes, or its field: the text nearest it, else its page's control. */
export function focusAfterNewText(
  { layout, reading }: { layout: Pick<Layout, "pages">; reading: Pick<Reading, "order"> },
  { page, origin: [x, y] }: Pick<Insert, "page" | "origin">,
): FocusTo {
  const near = nearestSpan(layout.pages.get(page) ?? [], [x, y]);
  return near === undefined ? pageControlOf(reading, page) : { spanId: near.id };
}

/** A face chosen in the slip with a pointer: focus goes back to the field, where the typing is. */
export function backToField(editor: Editor): void {
  editor.store.set({ focusTo: { field: true } });
}

/** Open new text where `at` was pressed on bare paper, styled as the text nearest it. */
export function openNewTextAt(editor: Editor, page: number, at: PagePoint): void {
  const { layout, reading } = editor.store.get();
  const near = nearestSpan(layout.pages.get(page) ?? [], at);
  const style = styleOf(near);
  const origin = originAt(at, style.size);
  const back = focusAfterNewText({ layout, reading }, { page, origin: [origin[0], origin[1]] });
  open(editor, { kind: "newText", page, of: null, origin, text: "", style, nearId: near?.id ?? null, back });
}

/** Open new text on the line below a span, at its left edge, in its style; focus comes back to it with Escape. */
export function openNewTextBelow(editor: Editor, spanId: string): void {
  const span = editor.store.get().layout.spans.get(spanId);
  if (span === undefined) return;
  const [, baseline] = span.origin;
  const origin: PagePoint = [span.bbox.x0, (baseline ?? span.bbox.y1) + span.size * LINE_BELOW_EM];
  const draft = { page: span.page, of: null, origin, text: "", style: styleOf(span), nearId: span.id };
  open(editor, { kind: "newText", ...draft, back: { spanId } });
}

/** Open new text at the top left of a page with no text, inside its margin; focus comes back to its control with Escape. */
export function openNewTextOnBarePage(editor: Editor, page: number): void {
  const { reading } = editor.store.get();
  const style = styleOf(undefined);
  const origin: PagePoint = [NEW_TEXT_MARGIN_PT, NEW_TEXT_MARGIN_PT + style.size];
  open(editor, {
    kind: "newText",
    page,
    of: null,
    origin,
    text: "",
    style,
    nearId: null,
    back: pageControlOf(reading, page),
  });
}

/** Type into new text again, from its mark: Enter, or a click. */
export function openNewTextAgain(editor: Editor, newTextId: number): void {
  const { reading, layout, shippedFaces } = editor.store.get();
  const text = keptNewTexts(reading).find((insert) => insert.newTextId === newTextId);
  if (text === undefined) return;
  const { page, origin, font_object: fontObject = null, size, color = BLACK } = text.edit;
  const font = fontNameOf(text.edit);
  const handwriting = inShippedFace(text.edit) && shippedFaces.handwriting.includes(font);
  const nearSize = handwriting ? size / HANDWRITING_STEP : size;
  const [x, y] = origin;
  const near = nearestSpan(layout.pages.get(page) ?? [], [x, y]);
  const style = { font, fontObject, size, nearSize, color };
  open(editor, {
    kind: "newText",
    page,
    of: text,
    origin,
    text: text.edit.text,
    style,
    nearId: near?.id ?? null,
    back: focusAfterNewText({ layout, reading }, text.edit),
  });
}

/** Open `draft`'s field, its line's start on its page: an empty one has no width yet. */
function open(editor: Editor, draft: NewTextDraft): void {
  const info = editor.store.get().doc.pages[draft.page];
  const origin =
    info === undefined ? draft.origin : keptOnPage(info, { origin: draft.origin, size: draft.style.size }, 0);
  editor.store.set({ draft: { ...draft, origin }, focusTo: null, unredacting: null });
}

/**
 * Move new text by `by` points, its whole line kept on its page as wide as it draws (`widthPt`): a drag, a step of
 * its own; a nudge, one with the nudges just before it.
 */
export function moveNewText(
  editor: Editor,
  newTextId: number,
  { by: [dx, dy], nudge, widthPt }: { by: PagePoint; nudge: boolean; widthPt: number },
): void {
  const { reading, doc, nudged } = editor.store.get();
  const text = keptNewTexts(reading).find((insert) => insert.newTextId === newTextId);
  const info = text === undefined ? undefined : doc.pages[text.edit.page];
  if (text === undefined || info === undefined) return;
  const [x, y] = text.edit.origin;
  const origin = keptOnPage(info, { origin: [x + dx, y + dy], size: text.edit.size }, widthPt);
  const edit = { ...text.edit, origin: [origin[0], origin[1]] as [number, number] };
  const folds = nudge && nudged === text.id;
  const next = editor.store.get().history.next;
  change(
    editor,
    { kind: "revise", replaces: text.id, edit, folds },
    {
      nudged: nudge ? next : null,
      focusTo: { newTextId },
      announcement: announcing(fill(doc.sentences.new_text_moved, { text: text.edit.text })),
    },
  );
}

/** Where a line of new text `widthPt` wide starts so the whole of it is on its page: what runs off draws nowhere. */
export function keptOnPage(
  info: PageInfo,
  { origin: [x, y], size }: { origin: PagePoint; size: number },
  widthPt: number,
): PagePoint {
  // Wider than the page, it starts at its left edge.
  const clamp = (value: number, low: number, high: number) => Math.max(Math.min(value, high), low);
  const top = size * NEW_TEXT_ASCENT_EM;
  return [clamp(x, 0, info.width - widthPt), clamp(y, top, info.height - size * NEW_TEXT_DESCENT_EM)];
}

/** Take new text out, every version of it, from its margin note: focus goes to the text nearest it. */
export function takeOutNewText(editor: Editor, newTextId: number): void {
  const { history, reading, layout, doc } = editor.store.get();
  const text = keptNewTexts(reading).find((insert) => insert.newTextId === newTextId);
  if (text === undefined) return;
  const focusTo = focusAfterNewText({ layout, reading }, text.edit);
  change(
    editor,
    { kind: "remove", ids: versionsOf(history, newTextId) },
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
