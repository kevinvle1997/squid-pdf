// Where things sit on a page, as CSS, and how wide text draws there. Everything inside the page's layer
// is in the page's own points, turned into shares of its size, so it scales with the page however wide it's shown.
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

/** Where a span's mark sits: over its line as drawn, so a replacement left long is pressed along its length. */
export function markBoxOf(
  span: SpanInfo,
  { replaced, fit }: { replaced: boolean; fit: FitInfo | undefined },
): SpanInfo["bbox"] {
  // A way out ends the line where the original did, and a shorter one keeps its place to press.
  const runsLonger = replaced && fit !== undefined && fit.strategy === "as-is" && fit.delta_pt > 0;
  return runsLonger ? { ...span.bbox, x1: span.bbox.x1 + fit.delta_pt } : span.bbox;
}

/** A box on the page as percentages of it, so it scales with the page. */
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

/** How far down the page `yPt` is, as a share of its height. */
export function topOf(yPt: number, page: { height: number }): { top: string } {
  return { top: `${(yPt / page.height) * 100}%` };
}

/**
 * Where something set under a box sits: just below it, pinned at the box's left edge, or, for a box in
 * the page's right half, at its right edge, so what's set there stays on the page however wide it is.
 */
export function pinnedUnder(
  bbox: { x0: number; x1: number; y1: number },
  page: { width: number },
): { top: string } & PinnedSide {
  return { top: points(bbox.y1 + SLIP_GAP_PT, page), ...pinnedSideOf(bbox, page) };
}

type PinnedSide = { left: string; right?: undefined } | { right: string; left?: undefined };

/** The edge of a box something set beside it keeps: its left, or for a box in the page's right half, its right. */
function pinnedSideOf(bbox: { x0: number; x1: number }, page: { width: number }): PinnedSide {
  if (bbox.x0 > page.width / 2) return { right: `${((page.width - bbox.x1) / page.width) * 100}%` };
  return { left: `${(bbox.x0 / page.width) * 100}%` };
}

/** A slip's width and height, in the page's points as it's shown. */
export interface SlipSize {
  readonly width: number;
  readonly height: number;
}

/** Which side of its line, as it's shown, a slip sits on. */
export type Side = "below" | "above" | "left" | "right";

/** How far the window reaches past the shown page's left and right edges, less its gutter, in the page's points. */
export interface WindowRoom {
  readonly left: number;
  readonly right: number;
}

/**
 * Where a slip may sit about its line as the page is turned, the side under the line first: then the line's
 * other side, then before its start and past its end, which the field grows towards. Below has room always.
 */
const SIDES_BY_TURN: Record<PageInfo["turn_cw"], readonly Side[]> = {
  0: ["below"],
  90: ["left", "right", "above", "below"],
  180: ["above", "below"],
  270: ["right", "left", "below"],
};

/**
 * Where a slip about `line` sits on its page's upright layer (`uprightOf`): under the line, at its start, as the
 * page is turned, and never turned itself, so its words read across. Once its `size` is known, it sits on `side`
 * of the line, else the side `slipSideOf` chooses, never over the line as drawn, which runs to `endPt` across the
 * page, as a field typed long does.
 */
export function slipPlaceOf(
  line: SpanInfo["bbox"],
  page: PageInfo,
  {
    size,
    endPt = line.x1,
    side,
    room = { left: 0, right: 0 },
  }: { size?: SlipSize | undefined; endPt?: number; side?: Side | undefined; room?: WindowRoom } = {},
): CSSProperties {
  if (size === undefined) return hungFrom(line, page);
  const shown = uprightOf(page);
  const sideTaken = side ?? slipSideOf(line, page, { size, endPt });
  const place = placeBeside(boxesOf(line, { page, endPt }), { side: sideTaken, size, page });
  // With no room on the page, one over or under its line keeps to the window: wider than the page, it would run
  // past the screen's edge. One beside its line keeps its place, which is off the line.
  const along = sideTaken === "above" || sideTaken === "below";
  const keptToWindow = along && !hasRoom(place, { size, pageWidth: shown.width });
  const left = keptToWindow
    ? clamp(place.left, { least: -room.left, most: shown.width + room.right - size.width })
    : place.left;
  // Moved from the layer's corner, so it's laid out in the same room wherever it sits, and keeps its size: the
  // corner on the side facing its line, so as it grows, it grows away from the line.
  const across = points(left, shown);
  const down = points(place.top, shown);
  if (sideTaken === "above") {
    return { left: 0, bottom: 0, translate: `${across} ${points(place.top + size.height - shown.height, shown)}` };
  }
  if (sideTaken === "left") {
    return { right: 0, top: 0, translate: `${points(left + size.width - shown.width, shown)} ${down}` };
  }
  return { left: 0, top: 0, translate: `${across} ${down}` };
}

/**
 * The side of its line a slip `size` big sits on: the first as the page is turned (`SIDES_BY_TURN`) where it's whole
 * on the page's width and below its top, else below, the last of every order.
 */
export function slipSideOf(
  line: SpanInfo["bbox"],
  page: PageInfo,
  { size, endPt = line.x1 }: { size: SlipSize; endPt?: number },
): Side {
  const boxes = boxesOf(line, { page, endPt });
  const pageWidth = uprightOf(page).width;
  const fits = (side: Side) => hasRoom(placeBeside(boxes, { side, size, page }), { size, pageWidth });
  return SIDES_BY_TURN[page.turn_cw].find(fits) ?? "below";
}

/** The line's own box as it's shown, and as it's drawn, running to `endPt`. */
function boxesOf(line: SpanInfo["bbox"], { page, endPt }: { page: PageInfo; endPt: number }) {
  return { own: shownBoxOf(line, page), drawn: shownBoxOf({ ...line, x1: Math.max(line.x1, endPt) }, page) };
}

/**
 * Where a slip hangs before its size is known: from the point under the line's start, by the corner that keeps
 * it off the line as the page is turned. A line running down the screen has its slip to the left.
 */
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

/** A box on the upright page, in its points. */
interface ShownBox {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

/** Where `line` shows once the page is turned, on the upright page. */
function shownBoxOf(line: SpanInfo["bbox"], page: PageInfo): ShownBox {
  const [x0, y0] = shownPointOf([line.x0, line.y0], page);
  const [x1, y1] = shownPointOf([line.x1, line.y1], page);
  return { left: Math.min(x0, x1), top: Math.min(y0, y1), right: Math.max(x0, x1), bottom: Math.max(y0, y1) };
}

/**
 * Where a slip `size` big sits on `side` of a line, its `own` box and as `drawn`, shown on the upright page: lined
 * up with the line's start, as it hangs before its size is known, then moved along the line to stay on the page.
 */
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
      // Lined up with the line's start: its top turned a quarter clockwise, its foot turned the other way.
      const top = page.turn_cw === 270 ? own.bottom - size.height : own.top;
      return { left, top: Math.max(0, top) };
    }
    default:
      return side satisfies never;
  }
}

/** Where a slip over or under its line starts across the page: at the line's start, as the page is turned. */
function leftAlongOf(box: ShownBox, { size, page }: { size: SlipSize; page: PageInfo }): number {
  // Unturned, it's pinned as what's set under a box is (`pinnedSideOf`).
  if (page.turn_cw === 0) return box.left > page.width / 2 ? box.right - size.width : box.left;
  if (page.turn_cw === 270) return box.left;
  return box.right - size.width;
}

/** Whether a slip `size` big at `place` is whole on the page's width and below its top: past its foot is room. */
function hasRoom(place: { left: number; top: number }, { size, pageWidth }: { size: SlipSize; pageWidth: number }) {
  return place.left >= 0 && place.left + size.width <= pageWidth && place.top >= 0;
}

function clamp(value: number, { least, most }: { least: number; most: number }): number {
  return Math.max(least, Math.min(value, most));
}

/** Where a point on the page, in its own points, shows once the page is turned: in the upright page's points. */
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

/**
 * The whole page's image, set inside a box on it so the box shows its own pixels: as wide as the page
 * is to the box, and moved up and left by where the box starts. Percentages of the box, as it scales.
 */
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

/** A length in points as CSS, against the page's width: the page layer is a size container. */
export function points(value: number, page: { width: number }): string {
  return `${(value / page.width) * 100}cqw`;
}

/** The page's width and height as shown, in points: the file's /Rotate turns a page on its side. */
function shownSize(page: PageInfo): [wide: number, tall: number] {
  const turned = page.turn_cw % 180 !== 0;
  return turned ? [page.height, page.width] : [page.width, page.height];
}

/** How wide the page is printed, in CSS pixels: as it shows at 100%. */
export function printedPxOf(page: PageInfo): number {
  const [wide] = shownSize(page);
  return wide * PX_PER_PT;
}

/** The page's printed width, which its column and its most width are built from, zoomed (`--zoom`). */
export function sheetWidthOf(page: PageInfo): { "--sheet-width": string } {
  return { "--sheet-width": `${printedPxOf(page)}px` };
}

/** The sheet's shape as the page is shown, so its box is held before the image comes. */
export function sheetShapeOf(page: PageInfo): CSSProperties {
  const [wide, tall] = shownSize(page);
  return { aspectRatio: `${wide} / ${tall}` };
}

/** The page as it's shown, upright: its width and height swapped where it's turned a quarter, and no turn. */
export function uprightOf(page: PageInfo): PageInfo {
  const [wide, tall] = shownSize(page);
  return { width: wide, height: tall, turn_cw: 0 };
}

/** The page's own layer in its sheet: the page's size in unrotated points, turned as the file says. */
export function layerOf(page: PageInfo): CSSProperties {
  const [wide, tall] = shownSize(page);
  return {
    width: `${(page.width / wide) * 100}%`,
    height: `${(page.height / tall) * 100}%`,
    transform: `translate(-50%, -50%) rotate(${page.turn_cw}deg)`,
  };
}

/**
 * How far a field set at `fontPx` scales down to show a span's `sizePt` on a page `pageWidthPt`
 * wide, shown `shownPx` wide: what a touch screen's field is drawn at.
 */
export function fieldScaleOf(
  sizePt: number,
  { pageWidthPt, shownPx, fontPx }: { pageWidthPt: number; shownPx: number; fontPx: number },
): number {
  // Not laid out yet: a scale of nothing would hide the field.
  if (fontPx <= 0 || pageWidthPt <= 0 || shownPx <= 0) return 1;
  return ((sizePt / pageWidthPt) * shownPx) / fontPx;
}

/** A PDF colour, each part from 0 to 1, as CSS. */
function rgbOf(color: readonly number[]): string {
  const [red = 0, green = 0, blue = 0] = color;
  return `rgb(${red * 255} ${green * 255} ${blue * 255})`;
}

/** Text drawn where a span is, as the page has it: its place, its size and colour, in `face`. */
export function spanTextStyle(span: SpanInfo, page: PageInfo, face: string): CSSProperties {
  const box = boxOf(span.bbox, page);
  return {
    left: box.left,
    top: box.top,
    height: box.height,
    fontSize: points(span.size, page),
    fontFamily: familyOf(face),
    color: rgbOf(span.color),
    // In ems of its size, so a way out that sets it smaller spaces it closer, as the server does.
    letterSpacing: span.letter_spacing_em === 0 ? undefined : `${span.letter_spacing_em}em`,
  };
}

/** Text in a span's place set at `size` and `scaleX`, shares of its own (a way out of a long line), as the page will draw it. */
function setAsStyle(span: SpanInfo, page: PageInfo, { size, scaleX }: LineScale): CSSProperties {
  // Narrowed from its start, as the server narrows the line.
  const narrowed = scaleX === 1 ? {} : { transform: `scaleX(${scaleX})`, transformOrigin: "0 0" };
  return { fontSize: points(span.size * size, page), ...narrowed };
}

/** The browser's drawing of a span's words, in its place as the page will draw them: in `face`, set at `scale`. */
export function previewStyleOf(
  span: SpanInfo,
  page: PageInfo,
  { face, scale }: { face: string; scale: LineScale },
): CSSProperties {
  return {
    ...spanTextStyle(span, page, face),
    ...setAsStyle(span, page, scale),
    minWidth: boxOf(span.bbox, page).width,
    // A length, not the box's share of the page: CSS reads a line height in percent against the font size.
    lineHeight: points(span.bbox.y1 - span.bbox.y0, page),
  };
}

// One canvas measures every field's text: it draws nothing, so it never joins the page.
let ruler: CanvasRenderingContext2D | null | undefined;

/** How wide `text` draws in `face` at `sizePt`, in points, as the browser sets it: a letter `face` lacks in its fallback. */
function shownWidthPt(text: string, face: string, sizePt: number): number {
  if (ruler === undefined) ruler = document.createElement("canvas").getContext("2d");
  if (ruler == null) return 0;
  // Width grows with size, so text set at `sizePt` px measures in points.
  ruler.font = `${sizePt}px ${familyOf(face)}`;
  return ruler.measureText(text).width;
}

/** How wide the field typing `text` over a span is, in points: never narrower than the span, with room past what's typed. */
export function fieldWidthPt(
  span: SpanInfo,
  { text, face, glyphs }: { text: string; face: string; glyphs: Record<string, number> },
): number {
  // The file's widths count a letter its font lacks as nothing, which the browser draws in a fallback.
  const shownPt = shownWidthPt(text, face, span.size) + letterSpacingPt(span, text);
  const typedPt = Math.max(lineWidthPt(span, text, glyphs), shownPt);
  return Math.max(span.bbox.x1 - span.bbox.x0, typedPt + span.size * FIELD_SPARE_EM);
}

/** Where `start` to `end` of the field's `text` sits on the page, as the field sets the text: in `face`, at `scale`. */
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

/** How wide new text's field is, in points: as wide as it draws, never narrower than a couple of letters, and room for the caret. */
export function newTextFieldWidthPt({ size }: Pick<Insert, "size">, widthPt: number): number {
  return Math.max(widthPt, size * NEW_TEXT_FIELD_MIN_EM) + size * FIELD_SPARE_EM;
}

/** As far past `box`'s edges as a strip reaches, inside the page: what the server redraws round new text. */
export function paddedBoxOf(box: Box, info: PageInfo): Box {
  return {
    x0: Math.max(0, box.x0 - STRIP_PAD_PT),
    y0: Math.max(0, box.y0 - STRIP_PAD_PT),
    x1: Math.min(info.width, box.x1 + STRIP_PAD_PT),
    y1: Math.min(info.height, box.y1 + STRIP_PAD_PT),
  };
}

/**
 * The line the slip of new text being typed hangs under, `fieldWidthPt` across: as tall as a handwriting face sets
 * it, the larger, so choosing a face never moves the slip from under the pointer.
 */
export function newTextSlipLineOf(
  insert: Pick<Insert, "origin" | "size">,
  { nearSize, fieldWidthPt }: { nearSize: number; fieldWidthPt: number },
): Box {
  return newTextBoxOf({ ...insert, size: Math.max(insert.size, nearSize * HANDWRITING_STEP) }, fieldWidthPt);
}

/** New text's words as they're set, wherever the box they're drawn in is: dragged, they go with the pointer. */
export function newTextWordsStyleOf(insert: Pick<Insert, "origin" | "size" | "color">, page: PageInfo, face: string) {
  const { left: _left, top: _top, height: _height, ...words } = newTextStyleOf(insert, page, face);
  return words;
}

/** The box new text is drawn in, from its baseline's start: `widthPt` across, an em above its baseline and a little below. */
export function newTextBoxOf(insert: Pick<Insert, "origin" | "size">, widthPt: number): Box {
  const line = newTextLineOf(insert);
  return { ...line, x1: line.x0 + widthPt };
}

/** The box new text is drawn in on its page, as wide as the face that previews it sets it. */
export function newTextBoxIn(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): Box {
  return newTextBoxOf(insert, newTextWidthIn(layout, insert));
}

/** How wide new text draws on its page, in points: in the face that previews it, by its page font's widths if it has one. */
export function newTextWidthIn(layout: Pick<Layout, "fonts" | "spans">, insert: Insert): number {
  const glyphs = fontOfNewText(layout, insert)?.glyphs ?? {};
  return newTextWidthPt(insert, { face: newTextFaceOf(layout, insert), glyphs });
}

/** How wide new text draws, in points: by its font's widths where the browser has them, else as `face` sets it here. */
export function newTextWidthPt(
  { text, size }: Pick<Insert, "text" | "size">,
  { face, glyphs }: { face: string; glyphs: Record<string, number> },
): number {
  return Math.max(widthPt(text, glyphs, size), shownWidthPt(text, face, size));
}

/** New text where it's drawn, as the page will draw it: from its baseline's start, at its size and colour, in `face`. */
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
