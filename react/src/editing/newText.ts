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
import {
  announcing,
  change,
  type Editor,
  type EditorState,
  type FocusNewText,
  type FocusSpan,
  type FocusTo,
  type Layout,
} from "./editor";
import { DEFAULT_FACE, isShippedFace, previewFaceOf } from "./faces";
import { missingIn } from "./fit";
import { versionsOf } from "./history";
import { type MismatchPlace, mismatchKeyOf } from "./mismatches";
import { inPageOrder, placedOnPage } from "./page";
import { pageControlOf } from "./pageList";
import type { NewTextReading, Reading } from "./project";
import type { PageNewTextFits } from "./render";
import { fontOf } from "./spanFonts";
import { type Drawing, drawingIn, drawingOf } from "./substitutes";
import { EXACT, liveVerdictOf, type Verdict } from "./verdicts";
import { fill } from "./words";

export type PagePoint = readonly [x: number, y: number];

type Color = NonNullable<Insert["color"]>;
const BLACK: Color = [0, 0, 0];

export interface NewTextStyle {
  readonly font: string;
  readonly fontObject: number | null;
  readonly size: number;
  readonly nearSize: number;
  readonly color: Color;
}

export interface NewTextDraft {
  readonly kind: "newText";
  readonly page: number;
  readonly of: NewTextReading | null;
  readonly origin: PagePoint;
  readonly text: string;
  readonly style: NewTextStyle;
  readonly nearId: string | null;
  readonly back: FocusTo | null;
}

export function nearestSpan(spans: readonly SpanInfo[], [x, y]: PagePoint): SpanInfo | undefined {
  const lines = spans.filter((span) => !span.form_field);
  const across = (span: SpanInfo) => Math.max(span.bbox.x0 - x, 0, x - span.bbox.x1);
  const down = (span: SpanInfo) => Math.max(span.bbox.y0 - y, 0, y - span.bbox.y1);
  const above = lines.filter((span) => span.bbox.y1 <= y);
  if (above.length > 0) return closest(above, (span) => y - span.bbox.y1 + across(span));
  return closest(lines, (span) => Math.hypot(across(span), down(span)));
}

function closest(spans: readonly SpanInfo[], distance: (span: SpanInfo) => number): SpanInfo | undefined {
  return spans.reduce<SpanInfo | undefined>(
    (best, span) => (best === undefined || distance(span) < distance(best) ? span : best),
    undefined,
  );
}

export function styleOf(span: SpanInfo | undefined): NewTextStyle {
  if (span === undefined) {
    return { font: NEW_TEXT_FACE, fontObject: null, size: NEW_TEXT_SIZE_PT, nearSize: NEW_TEXT_SIZE_PT, color: BLACK };
  }
  const [red = 0, green = 0, blue = 0] = span.color;
  const color: Color = [red, green, blue];
  return { font: span.font, fontObject: span.font_object, size: span.size, nearSize: span.size, color };
}

export function originAt([x, y]: PagePoint, size: number): PagePoint {
  return [x, y + (size * (NEW_TEXT_ASCENT_EM - NEW_TEXT_DESCENT_EM)) / 2];
}

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

export function fontNameOf(insert: Pick<Insert, "font">): string {
  return insert.font ?? NEW_TEXT_FACE;
}

export function inShippedFace(insert: Pick<Insert, "font" | "font_object">): boolean {
  return (insert.font_object ?? null) === null && isShippedFace(fontNameOf(insert));
}

export function fontOfNewText(layout: Pick<Layout, "fonts">, insert: Insert): FontInfo | undefined {
  if (inShippedFace(insert)) return undefined;
  return fontOf(layout.fonts, { font: fontNameOf(insert), font_object: insert.font_object ?? null });
}

export function newTextLineOf({ origin: [x, y], size }: { origin: PagePoint; size: number }): Box {
  return { x0: x, y0: y - size * NEW_TEXT_ASCENT_EM, x1: x, y1: y + size * NEW_TEXT_DESCENT_EM };
}

export type Box = { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };

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
    turned: false, // drawn level, as the server draws new text
  };
}

export function newTextSpanOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): SpanInfo {
  const font = fontOfNewText(layout, insert);
  const key = insert.font_object ?? fontNameOf(insert);
  const inFont = [...layout.spans.values()].find((span) => (span.font_object ?? span.font) === key);
  return asSpanOf(insert, { font, inFile: inFont?.in_file ?? font?.substitute == null });
}

export function newTextVerdictOf(
  layout: Pick<Layout, "fonts" | "spans">,
  { insert, fit }: { insert: Insert; fit: FitInfo | undefined },
): Verdict {
  if (fit !== undefined) return fit;
  if (inShippedFace(insert)) return shippedVerdictOf(insert);
  return liveVerdictOf(newTextSpanOf(layout, insert), { font: fontOfNewText(layout, insert), text: insert.text });
}

function shippedVerdictOf(insert: Insert): Verdict {
  const { face } = drawingIn(fontNameOf(insert), insert.text);
  if (face === fontNameOf(insert)) return EXACT;
  return { fidelity: "substitute", why: null, substitute: face, same_widths: false };
}

export function newTextDrawingOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): Drawing {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text);
  const font = fontOfNewText(layout, insert);
  if (font === undefined) return { face: DEFAULT_FACE, switched: [], leftOut: [] };
  const span = newTextSpanOf(layout, insert);
  return drawingOf(span, { font, text: insert.text, missing: missingIn(insert.text, font.glyphs) });
}

export function newTextFaceOf(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): string {
  if (inShippedFace(insert)) return drawingIn(fontNameOf(insert), insert.text).face;
  const font = fontOfNewText(layout, insert);
  return font === undefined ? DEFAULT_FACE : previewFaceOf(newTextSpanOf(layout, insert), { font, text: insert.text });
}

export function newTextPlaceOf(
  layout: Pick<Layout, "fonts" | "spans">,
  { text, fit }: { text: NewTextReading; fit: FitInfo | undefined },
): MismatchPlace | undefined {
  const verdict = newTextVerdictOf(layout, { insert: text.edit, fit });
  if (verdict.fidelity === "exact") return undefined;
  const missing = fit?.missing ?? newTextDrawingOf(layout, text.edit).switched;
  return { span: newTextSpanOf(layout, text.edit), verdict, missing };
}

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

export function keptNewTexts(reading: Reading): NewTextReading[] {
  return [...reading.pages].flatMap(([page, edits]) => (reading.deleted.has(page) ? [] : edits.newTexts));
}

export function focusAfterNewText(
  { layout, reading }: { layout: Pick<Layout, "pages">; reading: Pick<Reading, "order"> },
  { page, origin: [x, y] }: Pick<Insert, "page" | "origin">,
): FocusTo {
  const near = nearestSpan(layout.pages.get(page) ?? [], [x, y]);
  return near === undefined ? pageControlOf(reading, page) : { spanId: near.id };
}

export function backToField(editor: Editor): void {
  editor.store.set({ focusTo: { field: true } });
}

export function toFaces(editor: Editor): void {
  editor.store.set({ focusTo: { faces: true } });
}

export function pastFaces(editor: Editor): boolean {
  const next = afterNewTextField(editor.store.get());
  if (next === null) return false;
  editor.store.set({ focusTo: next });
  return true;
}

export function afterNewTextField({
  draft,
  layout,
  reading,
}: Pick<EditorState, "draft" | "layout" | "reading">): FocusSpan | FocusNewText | null {
  if (draft?.kind !== "newText") return null;
  const placed = placedOnPage({
    edits: reading.pages.get(draft.page),
    newTextAt: draft.origin,
    newTextTypedAgain: draft.of?.newTextId ?? null,
  });
  const marks = inPageOrder(layout.pages.get(draft.page) ?? [], placed);
  const next = marks[marks.findIndex((mark) => mark.placed === "field") + 1];
  if (next === undefined) return null;
  if (next.span !== undefined) return { spanId: next.span.id };
  return next.placed === "field" ? null : { newTextId: next.placed.newTextId };
}

export function openNewTextAt(editor: Editor, page: number, at: PagePoint): void {
  const { layout, reading } = editor.store.get();
  const near = nearestSpan(layout.pages.get(page) ?? [], at);
  const style = styleOf(near);
  const origin = originAt(at, style.size);
  const back = focusAfterNewText({ layout, reading }, { page, origin: [origin[0], origin[1]] });
  open(editor, { kind: "newText", page, of: null, origin, text: "", style, nearId: near?.id ?? null, back });
}

export function openNewTextBelow(editor: Editor, spanId: string): void {
  const span = editor.store.get().layout.spans.get(spanId);
  if (span === undefined) return;
  const [, baseline] = span.origin;
  const origin: PagePoint = [span.bbox.x0, (baseline ?? span.bbox.y1) + span.size * LINE_BELOW_EM];
  const draft = { page: span.page, of: null, origin, text: "", style: styleOf(span), nearId: span.id };
  open(editor, { kind: "newText", ...draft, back: { spanId } });
}

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

function open(editor: Editor, draft: NewTextDraft): void {
  const info = editor.store.get().doc.pages[draft.page];
  const origin =
    info === undefined ? draft.origin : keptOnPage(info, { origin: draft.origin, size: draft.style.size }, 0);
  editor.store.set({ draft: { ...draft, origin }, focusTo: null, unredacting: null });
}

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

export function keptOnPage(
  info: PageInfo,
  { origin: [x, y], size }: { origin: PagePoint; size: number },
  widthPt: number,
): PagePoint {
  const clamp = (value: number, low: number, high: number) => Math.max(Math.min(value, high), low);
  const top = size * NEW_TEXT_ASCENT_EM;
  return [clamp(x, 0, info.width - widthPt), clamp(y, top, info.height - size * NEW_TEXT_DESCENT_EM)];
}

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

export function pointOnPage(
  [clientX, clientY]: PagePoint,
  { shown, info }: { shown: { left: number; top: number; width: number; height: number }; info: PageInfo },
): PagePoint {
  const [dx, dy] = unturned([clientX - (shown.left + shown.width / 2), clientY - (shown.top + shown.height / 2)], info);
  const pxPerPt = (info.turn_cw % 180 === 0 ? shown.width : shown.height) / info.width;
  return [info.width / 2 + dx / pxPerPt, info.height / 2 + dy / pxPerPt];
}

export function moveOnPage(by: PagePoint, { shownWidthPx, info }: { shownWidthPx: number; info: PageInfo }): PagePoint {
  const [dx, dy] = unturned(by, info);
  const pxPerPt = shownWidthPx / info.width;
  return pxPerPt > 0 ? [dx / pxPerPt, dy / pxPerPt] : [0, 0];
}

function unturned([dx, dy]: PagePoint, info: PageInfo): PagePoint {
  const angle = (-info.turn_cw * Math.PI) / 180;
  const [cos, sin] = [Math.round(Math.cos(angle)), Math.round(Math.sin(angle))];
  return [dx * cos - dy * sin, dx * sin + dy * cos];
}
