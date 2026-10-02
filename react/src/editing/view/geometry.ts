// Where things sit on a page, as CSS. Everything inside the page's layer is in the page's own
// points, turned into shares of its size, so it scales with the page however wide it's shown.
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

/** A length in points as CSS, against the page's width: the page layer is a size container. */
export function points(value: number, page: { width: number }): string {
  return `${(value / page.width) * 100}cqw`;
}

/** The page's width and height as shown, in points: the file's /Rotate turns a page on its side. */
export function shownSize(page: PageInfo): [wide: number, tall: number] {
  const turned = page.turn_cw % 180 !== 0;
  return turned ? [page.height, page.width] : [page.width, page.height];
}

/** A PDF colour, each part from 0 to 1, as CSS. */
export function rgbOf(color: readonly number[]): string {
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
