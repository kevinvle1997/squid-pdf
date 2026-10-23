// Where things sit on a page, as CSS, and how wide text draws there. Everything inside the page's layer
// is in the page's own points, turned into shares of its size, so it scales with the page however wide it's shown.
import type { CSSProperties } from "react";
import type { FitInfo, PageInfo, SpanInfo } from "../../api/types";
import { FIELD_SPARE_EM, PX_PER_PT, SLIP_GAP_PT } from "../constants";
import { familyOf } from "../faces";
import { type LineScale, letterSpacingPt, lineWidthPt } from "../fit";

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
): { top: string } & ({ left: string; right?: undefined } | { right: string; left?: undefined }) {
  const top = points(bbox.y1 + SLIP_GAP_PT, page);
  if (bbox.x0 > page.width / 2) return { top, right: `${((page.width - bbox.x1) / page.width) * 100}%` };
  return { top, left: `${(bbox.x0 / page.width) * 100}%` };
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
