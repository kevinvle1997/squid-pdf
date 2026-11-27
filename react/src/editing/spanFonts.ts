import type { FontInfo, SpanInfo } from "../api/types";

export type FontsByObject = ReadonlyMap<number | string, FontInfo>;

export function fontsByObject(fonts: readonly FontInfo[]): FontsByObject {
  return new Map(fonts.map((font) => [font.font_object ?? font.name, font]));
}

export function fontOf(fonts: FontsByObject, span: Pick<SpanInfo, "font" | "font_object">): FontInfo | undefined {
  return fonts.get(span.font_object ?? span.font);
}

export function shownNameIn(fonts: FontsByObject, span: Pick<SpanInfo, "font" | "font_object">): string {
  return fontOf(fonts, span)?.shown_name ?? span.font;
}
