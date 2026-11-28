import type { CSSProperties } from "react";
import type { FitInfo, Insert, PageInfo, SpanInfo } from "../../api/types";
import {
  FIELD_SPARE_EM,
  HANDWRITING_STEP,
  NEW_TEXT_ASCENT_EM,
  NEW_TEXT_DESCENT_EM,
  NEW_TEXT_FIELD_MIN_EM,
  PX_PER_PT,
  SLIP_GAP_PT,
  STRIP_PAD_PT,
} from "../constants";
import type { Layout } from "../editor";
import { familyOf } from "../faces";
import { type LineScale, letterSpacingPt, lineWidthPt, widthPt } from "../fit";
import { type Box, fontOfNewText, newTextFaceOf, newTextLineOf } from "../newText";

export function markBoxOf(
  span: Pick<SpanInfo, "bbox">,
  { replaced, fit }: { replaced: boolean; fit: FitInfo | undefined },
): SpanInfo["bbox"] {
  const runsLonger = replaced && fit !== undefined && fit.strategy === "as-is" && fit.delta_pt > 0;
  return runsLonger ? { ...span.bbox, x1: span.bbox.x1 + fit.delta_pt } : span.bbox;
}

export function boxOf(
  bbox: { x0: number; y0: number; x1: number; y1: number },
  page: { width: number; height: number },
): { left: string; top: string; width: string; height: string } {
  const across = (value: number) => `${(value / page.width) * 100}%`;
  const down = (value: number) => `${(value / page.height) * 100}%`;
  return {
    left: across(bbox.x0),
    top: down(bbox.y0),
    width: across(bbox.x1 - bbox.x0),
    height: down(bbox.y1 - bbox.y0),
  };
}

/** Where a mark's underlines hang from: its baseline, as a share of its box up from the box's foot. */
export function baselineOf(box: Box, baselineY: number): { "--below-baseline": string } {
  const height = box.y1 - box.y0;
  const below = height > 0 ? (box.y1 - baselineY) / height : 0;
  return { "--below-baseline": `${Math.min(Math.max(below, 0), 1) * 100}%` };
}

export function topOf(yPt: number, page: { height: number }): { top: string } {
  return { top: `${(yPt / page.height) * 100}%` };
}

export function pinnedUnder(
  bbox: { x0: number; x1: number; y1: number },
  page: { width: number },
): { top: string } & PinnedSide {
  return { top: points(bbox.y1 + SLIP_GAP_PT, page), ...pinnedSideOf(bbox, page) };
}

type PinnedSide = { left: string; right?: undefined } | { right: string; left?: undefined };

function pinnedSideOf(bbox: { x0: number; x1: number }, page: { width: number }): PinnedSide {
  if (bbox.x0 > page.width / 2) return { right: `${((page.width - bbox.x1) / page.width) * 100}%` };
  return { left: `${(bbox.x0 / page.width) * 100}%` };
}

export interface SlipSize {
  readonly width: number;
  readonly height: number;
}

export type Side = "below" | "above" | "left" | "right";

export interface WindowRoom {
  readonly left: number;
  readonly right: number;
  readonly top: number;
  readonly bottom: number;
  readonly pagesStart: number;
  readonly pagesEnd: number;
  readonly zoomControl: { readonly left: number; readonly bottom: number } | null;
}

const UNMEASURED: WindowRoom = {
  left: 0,
  right: 0,
  top: 0,
  bottom: Infinity,
  pagesStart: 0,
  pagesEnd: Infinity,
  zoomControl: null,
};

const SIDES_BY_TURN: Record<PageInfo["turn_cw"], readonly Side[]> = {
  0: ["below", "above"],
  90: ["left", "right", "above", "below"],
  180: ["above", "below"],
  270: ["right", "left", "below"],
};

export function slipPlaceOf(
  line: SpanInfo["bbox"],
  page: PageInfo,
  {
    size,
    endPt = line.x1,
    side,
    room = UNMEASURED,
  }: { size?: SlipSize | undefined; endPt?: number; side?: Side | undefined; room?: WindowRoom } = {},
): CSSProperties {
  if (size === undefined) return hungFrom(line, page);
  const shown = uprightOf(page);
  const sideTaken = side ?? slipSideOf(line, page, { size, endPt, room });
  const { left, top } = placedOn(boxesOf(line, { page, endPt }), { side: sideTaken, size, page, room });
  const across = points(left, shown);
  const down = points(top, shown);
  if (sideTaken === "above") {
    return { left: 0, bottom: 0, translate: `${across} ${points(top + size.height - shown.height, shown)}` };
  }
  if (sideTaken === "left") {
    return { right: 0, top: 0, translate: `${points(left + size.width - shown.width, shown)} ${down}` };
  }
  return { left: 0, top: 0, translate: `${across} ${down}` };
}

export function slipSideOf(
  line: SpanInfo["bbox"],
  page: PageInfo,
  {
    size,
    endPt = line.x1,
    room = UNMEASURED,
    held,
  }: { size: SlipSize; endPt?: number; room?: WindowRoom; held?: Side | undefined },
): Side {
  const boxes = boxesOf(line, { page, endPt });
  const shown = uprightOf(page);
  const placeOn = (side: Side) => placedOn(boxes, { side, size, page, room });
  const withinPages = (side: Side) => {
    const { top } = placeOn(side);
    return top >= -room.pagesStart && top + size.height <= shown.height + room.pagesEnd;
  };
  if (held !== undefined && withinPages(held)) return held;
  const onPage = SIDES_BY_TURN[page.turn_cw].filter((side) =>
    fitsPage(placeOn(side), { side, size, pageWidth: shown.width }),
  );
  const inWindow = (side: Side) => fitsWindow(placeOn(side), { size, shown, room });
  return onPage.find(inWindow) ?? onPage.find(withinPages) ?? onPage[0] ?? "below";
}

function boxesOf(line: SpanInfo["bbox"], { page, endPt }: { page: PageInfo; endPt: number }) {
  return { own: shownBoxOf(line, page), drawn: shownBoxOf({ ...line, x1: Math.max(line.x1, endPt) }, page) };
}

function hungFrom(line: SpanInfo["bbox"], page: PageInfo): CSSProperties {
  if (page.turn_cw === 0) return pinnedUnder(line, page);
  const shown = uprightOf(page);
  const [x, y] = shownPointOf([line.x0, line.y1 + SLIP_GAP_PT], page);
  const fromLeft = `${(x / shown.width) * 100}%`;
  const fromRight = `${((shown.width - x) / shown.width) * 100}%`;
  const fromBottom = `${((shown.height - y) / shown.height) * 100}%`;
  switch (page.turn_cw) {
    case 90:
      return { top: points(y, shown), right: fromRight };
    case 180:
      return { bottom: fromBottom, right: fromRight };
    case 270:
      return { bottom: fromBottom, left: fromLeft };
    default:
      return page.turn_cw satisfies never;
  }
}

interface ShownBox {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

function shownBoxOf(line: SpanInfo["bbox"], page: PageInfo): ShownBox {
  const [x0, y0] = shownPointOf([line.x0, line.y0], page);
  const [x1, y1] = shownPointOf([line.x1, line.y1], page);
  return { left: Math.min(x0, x1), top: Math.min(y0, y1), right: Math.max(x0, x1), bottom: Math.max(y0, y1) };
}

function placeBeside(
  { own, drawn }: { own: ShownBox; drawn: ShownBox },
  { side, size, page }: { side: Side; size: SlipSize; page: PageInfo },
): { left: number; top: number } {
  switch (side) {
    case "below":
    case "above": {
      const top = side === "below" ? drawn.bottom + SLIP_GAP_PT : drawn.top - SLIP_GAP_PT - size.height;
      const left = clamp(leftAlongOf(own, { size, page }), { least: 0, most: uprightOf(page).width - size.width });
      return { left, top };
    }
    case "left":
    case "right": {
      const left = side === "left" ? drawn.left - SLIP_GAP_PT - size.width : drawn.right + SLIP_GAP_PT;
      const top = page.turn_cw === 270 ? own.bottom - size.height : own.top;
      return { left, top: Math.max(0, top) };
    }
    default:
      return side satisfies never;
  }
}

function placedOn(
  boxes: { own: ShownBox; drawn: ShownBox },
  { side, size, page, room }: { side: Side; size: SlipSize; page: PageInfo; room: WindowRoom },
): { left: number; top: number } {
  const place = placeBeside(boxes, { side, size, page });
  const shown = uprightOf(page);
  const keptToWindow = overOrUnder(side) && !fitsAcross(place, { size, pageWidth: shown.width });
  if (!keptToWindow) return place;
  return { ...place, left: clamp(place.left, { least: -room.left, most: shown.width + room.right - size.width }) };
}

function leftAlongOf(box: ShownBox, { size, page }: { size: SlipSize; page: PageInfo }): number {
  if (page.turn_cw === 0) return box.left > page.width / 2 ? box.right - size.width : box.left;
  if (page.turn_cw === 270) return box.left;
  return box.right - size.width;
}

function fitsAcross(place: { left: number }, { size, pageWidth }: { size: SlipSize; pageWidth: number }) {
  return place.left >= 0 && place.left + size.width <= pageWidth;
}

function fitsPage(
  place: { left: number },
  { side, size, pageWidth }: { side: Side; size: SlipSize; pageWidth: number },
) {
  return overOrUnder(side) || fitsAcross(place, { size, pageWidth });
}

function overOrUnder(side: Side): boolean {
  return side === "above" || side === "below";
}

function fitsWindow(
  place: { left: number; top: number },
  { size, shown, room }: { size: SlipSize; shown: { height: number }; room: WindowRoom },
) {
  const zoom = room.zoomControl;
  const overZoom = zoom !== null && place.left + size.width > zoom.left;
  const bottom = overZoom ? Math.min(room.bottom, zoom.bottom) : room.bottom;
  return place.top >= -room.top && place.top + size.height <= shown.height + bottom;
}

function clamp(value: number, { least, most }: { least: number; most: number }): number {
  return Math.max(least, Math.min(value, most));
}

function shownPointOf([x, y]: [number, number], page: PageInfo): [number, number] {
  switch (page.turn_cw) {
    case 0:
      return [x, y];
    case 90:
      return [page.height - y, x];
    case 180:
      return [page.width - x, page.height - y];
    case 270:
      return [y, page.width - x];
    default:
      return page.turn_cw satisfies never;
  }
}

export function cropOf(
  box: { x0: number; y0: number; x1: number; y1: number },
  page: { width: number },
): { width: string; left: string; top: string } {
  const wide = box.x1 - box.x0;
  const tall = box.y1 - box.y0;
  return {
    width: `${(page.width / wide) * 100}%`,
    left: `${(-box.x0 / wide) * 100}%`,
    top: `${(-box.y0 / tall) * 100}%`,
  };
}

export function points(value: number, page: { width: number }): string {
  return `${(value / page.width) * 100}cqw`;
}

function shownSize(page: PageInfo): [wide: number, tall: number] {
  const turned = page.turn_cw % 180 !== 0;
  return turned ? [page.height, page.width] : [page.width, page.height];
}

export function printedPxOf(page: PageInfo): number {
  const [wide] = shownSize(page);
  return wide * PX_PER_PT;
}

export function sheetWidthOf(page: PageInfo): { "--sheet-width": string } {
  return { "--sheet-width": `${printedPxOf(page)}px` };
}

export function sheetShapeOf(page: PageInfo): CSSProperties {
  const [wide, tall] = shownSize(page);
  return { aspectRatio: `${wide} / ${tall}` };
}

export function uprightOf(page: PageInfo): PageInfo {
  const [wide, tall] = shownSize(page);
  return { width: wide, height: tall, turn_cw: 0 };
}

export function layerOf(page: PageInfo): CSSProperties {
  const [wide, tall] = shownSize(page);
  return {
    width: `${(page.width / wide) * 100}%`,
    height: `${(page.height / tall) * 100}%`,
    transform: `translate(-50%, -50%) rotate(${page.turn_cw}deg)`,
  };
}

export function fieldScaleOf(
  sizePt: number,
  { pageWidthPt, shownPx, fontPx }: { pageWidthPt: number; shownPx: number; fontPx: number },
): number {
  if (fontPx <= 0 || pageWidthPt <= 0 || shownPx <= 0) return 1;
  return ((sizePt / pageWidthPt) * shownPx) / fontPx;
}

function rgbOf(color: readonly number[]): string {
  const [red = 0, green = 0, blue = 0] = color;
  return `rgb(${red * 255} ${green * 255} ${blue * 255})`;
}

export function spanTextStyle(span: SpanInfo, page: PageInfo, face: string): CSSProperties {
  const box = boxOf(span.bbox, page);
  return {
    left: box.left,
    top: box.top,
    height: box.height,
    fontSize: points(span.size, page),
    fontFamily: familyOf(face),
    color: rgbOf(span.color),
    letterSpacing: span.letter_spacing_em === 0 ? undefined : `${span.letter_spacing_em}em`,
  };
}

function setAsStyle(span: SpanInfo, page: PageInfo, { size, scaleX }: LineScale): CSSProperties {
  const narrowed = scaleX === 1 ? {} : { transform: `scaleX(${scaleX})`, transformOrigin: "0 0" };
  return { fontSize: points(span.size * size, page), ...narrowed };
}

export function previewStyleOf(
  span: SpanInfo,
  page: PageInfo,
  { face, scale }: { face: string; scale: LineScale },
): CSSProperties {
  return {
    ...spanTextStyle(span, page, face),
    ...setAsStyle(span, page, scale),
    minWidth: boxOf(span.bbox, page).width,
    lineHeight: points(span.bbox.y1 - span.bbox.y0, page),
  };
}

export function runTextStyle(
  span: SpanInfo,
  { face, text, gapPt, scale, basePt }: { face: string; text: string; gapPt: number; scale: LineScale; basePt: number },
): CSSProperties {
  const sizePt = span.size * scale.size;
  const inEms = (lengthPt: number) => `${lengthPt / sizePt}em`;
  const set = {
    fontFamily: familyOf(face),
    color: rgbOf(span.color),
    letterSpacing: span.letter_spacing_em === 0 ? undefined : `${span.letter_spacing_em}em`,
    fontSize: `${sizePt / basePt}em`,
    marginInlineStart: inEms(gapPt),
  };
  if (scale.scaleX === 1) return set;
  const widthPt = shownWidthPt(text, face, sizePt) + letterSpacingPt({ ...span, size: sizePt }, text);
  const narrowed = { display: "inline-block", transform: `scaleX(${scale.scaleX})`, transformOrigin: "0 0" };
  return { ...set, ...narrowed, marginInlineEnd: inEms(-widthPt * (1 - scale.scaleX)) };
}

let ruler: CanvasRenderingContext2D | null | undefined;

function shownWidthPt(text: string, face: string, sizePt: number): number {
  if (ruler === undefined) ruler = document.createElement("canvas").getContext("2d");
  if (ruler == null) return 0;
  ruler.font = `${sizePt}px ${familyOf(face)}`;
  return ruler.measureText(text).width;
}

export function fieldWidthPt(
  span: SpanInfo,
  { text, face, glyphs }: { text: string; face: string; glyphs: Record<string, number> },
): number {
  // The file's widths count a letter its font lacks as nothing, which the browser draws in a fallback.
  const shownPt = shownWidthPt(text, face, span.size) + letterSpacingPt(span, text);
  const typedPt = Math.max(lineWidthPt(span, text, glyphs), shownPt);
  return Math.max(span.bbox.x1 - span.bbox.x0, typedPt + span.size * FIELD_SPARE_EM);
}

export function fieldPartOf(
  span: SpanInfo,
  { text, start, end, face, scale }: { text: string; start: number; end: number; face: string; scale: LineScale },
): SpanInfo["bbox"] {
  const setAt = { ...span, size: span.size * scale.size };
  const widthTo = (index: number) => {
    const before = text.slice(0, index);
    return (shownWidthPt(before, face, setAt.size) + letterSpacingPt(setAt, before)) * scale.scaleX;
  };
  return { ...span.bbox, x0: span.bbox.x0 + widthTo(start), x1: span.bbox.x0 + widthTo(end) };
}

export function newTextFieldWidthPt({ size }: Pick<Insert, "size">, widthPt: number): number {
  return Math.max(widthPt, size * NEW_TEXT_FIELD_MIN_EM) + size * FIELD_SPARE_EM;
}

export function paddedBoxOf(box: Box, info: PageInfo): Box {
  return {
    x0: Math.max(0, box.x0 - STRIP_PAD_PT),
    y0: Math.max(0, box.y0 - STRIP_PAD_PT),
    x1: Math.min(info.width, box.x1 + STRIP_PAD_PT),
    y1: Math.min(info.height, box.y1 + STRIP_PAD_PT),
  };
}

export function newTextSlipLineOf(
  insert: Pick<Insert, "origin" | "size">,
  { nearSize, fieldWidthPt }: { nearSize: number; fieldWidthPt: number },
): Box {
  return newTextBoxOf({ ...insert, size: Math.max(insert.size, nearSize * HANDWRITING_STEP) }, fieldWidthPt);
}

export function newTextWordsStyleOf(insert: Pick<Insert, "origin" | "size" | "color">, page: PageInfo, face: string) {
  const { left: _left, top: _top, height: _height, ...words } = newTextStyleOf(insert, page, face);
  return words;
}

export function newTextBoxOf(insert: Pick<Insert, "origin" | "size">, widthPt: number): Box {
  const line = newTextLineOf(insert);
  return { ...line, x1: line.x0 + widthPt };
}

export function newTextBoxIn(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): Box {
  return newTextBoxOf(insert, newTextWidthIn(layout, insert));
}

export function newTextWidthIn(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): number {
  const glyphs = fontOfNewText(layout, insert)?.glyphs ?? {};
  return newTextWidthPt(insert, { face: newTextFaceOf(layout, insert), glyphs });
}

export function newTextWidthPt(
  { text, size }: Pick<Insert, "text" | "size">,
  { face, glyphs }: { face: string; glyphs: Record<string, number> },
): number {
  return Math.max(widthPt(text, glyphs, size), shownWidthPt(text, face, size));
}

export function newTextStyleOf(
  insert: Pick<Insert, "origin" | "size" | "color">,
  page: PageInfo,
  face: string,
): CSSProperties {
  const box = boxOf(newTextBoxOf(insert, 0), page);
  return {
    left: box.left,
    top: box.top,
    height: box.height,
    fontSize: points(insert.size, page),
    lineHeight: points(insert.size * (NEW_TEXT_ASCENT_EM + NEW_TEXT_DESCENT_EM), page),
    fontFamily: familyOf(face),
    color: rgbOf(insert.color ?? [0, 0, 0]),
  };
}
