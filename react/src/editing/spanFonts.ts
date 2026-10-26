// The font that draws each span, as the server lists the document's fonts: by the font's PDF object.
import type { FontInfo, SpanInfo } from "../api/types";

/** The document's fonts by their PDF object, or by name for a font no page lists: none has an object. */
export type FontsByObject = ReadonlyMap<number | string, FontInfo>;

/** The document's fonts, each under its object, else its name. */
export function fontsByObject(fonts: readonly FontInfo[]): FontsByObject {
  return new Map(fonts.map((font) => [font.font_object ?? font.name, font]));
}

/** The font that draws `span`, by its object, else its name; undefined for one the server doesn't list. */
export function fontOf(fonts: FontsByObject, span: Pick<SpanInfo, "font" | "font_object">): FontInfo | undefined {
  return fonts.get(span.font_object ?? span.font);
}

/** The name the reader knows `span`'s font by: copies of one font share it. */
export function shownNameIn(fonts: FontsByObject, span: Pick<SpanInfo, "font" | "font_object">): string {
  // A font the server doesn't list goes by its own name.
  return fontOf(fonts, span)?.shown_name ?? span.font;
}
