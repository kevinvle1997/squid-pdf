// Where things sit on a page, as CSS, and how wide text draws there. Everything inside the page's layer
// is in the page's own points, turned into shares of its size, so it scales with the page however wide it's shown.
import type { CSSProperties } from "react";
import type { PageInfo, SpanInfo } from "../../api/types";
import { familyOf } from "../faces";

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

/**
 * Where something set under a box is pinned across the page: at the box's left edge, or, for a box in
 * the page's right half, at its right edge, so what's set there stays on the page however wide it is.
 */
export function pinnedUnder(
  bbox: { x0: number; x1: number },
  page: { width: number },
): { left: string; right?: undefined } | { right: string; left?: undefined } {
  if (bbox.x0 > page.width / 2) return { right: `${((page.width - bbox.x1) / page.width) * 100}%` };
  return { left: `${(bbox.x0 / page.width) * 100}%` };
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
export function shownSize(page: PageInfo): [wide: number, tall: number] {
  const turned = page.turn_cw % 180 !== 0;
  return turned ? [page.height, page.width] : [page.width, page.height];
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
  };
}

/** Text in a span's place set at `size` and `scaleX`, shares of its own (a way out of a long line), as the page will draw it. */
export function setAsStyle(
  span: SpanInfo,
  page: PageInfo,
  { size, scaleX }: { size: number; scaleX: number },
): CSSProperties {
  // Narrowed from its start, as the server narrows the line.
  const narrowed = scaleX === 1 ? {} : { transform: `scaleX(${scaleX})`, transformOrigin: "0 0" };
  return { fontSize: points(span.size * size, page), ...narrowed };
}

// One canvas measures every field's text: it draws nothing, so it never joins the page.
let ruler: CanvasRenderingContext2D | null | undefined;

/** How wide `text` draws in `face` at `sizePt`, in points, as the browser sets it: a letter `face` lacks in its fallback. */
export function shownWidthPt(text: string, face: string, sizePt: number): number {
  if (ruler === undefined) ruler = document.createElement("canvas").getContext("2d");
  if (ruler == null) return 0;
  // Width grows with size, so text set at `sizePt` px measures in points.
  ruler.font = `${sizePt}px ${familyOf(face)}`;
  return ruler.measureText(text).width;
}
