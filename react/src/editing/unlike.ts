// Where an edit won't match the text around it, grouped by why: each font a similar one draws for,
// then each reason the file's own font won't match. The bar's count opens it (quality.md, Rule 1).
import type { Copy, FontInfo, SpanInfo } from "../api/types";
import { substituteOf } from "./faces";
import { type Facts, fill, shownAs } from "./words";

/** A span an edit won't match at, and the letters its own font lacks for the words typed there, if those are why. */
export interface UnlikePlace {
  readonly span: SpanInfo;
  readonly missing: readonly string[];
}

/** One font a similar one draws for: its name as shown, how many places, and why, in the server's words. */
export interface UnlikeFont {
  readonly name: string;
  readonly places: number;
  readonly said: string;
}

/** One reason the file's own font won't match the text around it: how many places, and the server's sentence. */
export interface UnlikeReason {
  readonly code: NonNullable<SpanInfo["why"]>["code"];
  readonly places: number;
  readonly said: string;
}

/** Every place an edit won't match, grouped, each group most places first. */
export interface Unlike {
  readonly count: number;
  readonly fonts: readonly UnlikeFont[];
  readonly reasons: readonly UnlikeReason[];
}

/** `places` grouped: a similar font's under its font, the rest under why the file's own won't match. */
export function unlikeOf(
  places: readonly UnlikePlace[],
  { fonts, copy }: { fonts: ReadonlyMap<string, FontInfo>; copy: Copy },
): Unlike {
  const byFont = new Map<string, UnlikePlace[]>();
  const byReason = new Map<UnlikeReason["code"], SpanInfo[]>();
  for (const place of places) {
    const { span } = place;
    const why = span.why;
    // Drawn in a similar font: whatever else is unlike, its font is what the reader can change.
    if (span.substitute !== null || place.missing.length > 0 || why === null) {
      const name = fonts.get(span.font)?.shown_name ?? span.font;
      groupOf(byFont, name).push(place);
      continue;
    }
    groupOf(byReason, why.code).push(span);
  }
  const fontRows = [...byFont].map(([name, inFont]) => ({
    name,
    places: inFont.length,
    said: fontSaid(inFont, { fonts, copy }),
  }));
  const reasonRows = [...byReason].map(([code, spans]) => ({
    code,
    places: spans.length,
    said: reasonSaid(spans, copy),
  }));
  return { count: places.length, fonts: mostFirst(fontRows), reasons: mostFirst(reasonRows) };
}

/** Why a similar font draws a font's places: the font's own reason, else the letters it lacks, else what draws it. */
function fontSaid(
  places: UnlikePlace[],
  { fonts, copy }: { fonts: ReadonlyMap<string, FontInfo>; copy: Copy },
): string {
  const [first] = places;
  const font = first === undefined ? undefined : fonts.get(first.span.font);
  if (first === undefined || font === undefined) return "";
  if (font.why !== null) return font.why;
  const substitute = substituteOf(first.span, font);
  const chars = [...new Set(places.flatMap((place) => place.missing))].map((letter) =>
    shownAs(letter, copy.characters),
  );
  if (chars.length > 0) return fill(copy.missing, { chars, font: substitute });
  return fill(font.same_widths ? copy.substitute_same_widths : copy.substitute, { font: substitute });
}

/** A reason's sentence, its facts gathered from every place: the letters no font can draw at any of them. */
function reasonSaid(spans: SpanInfo[], copy: Copy): string {
  const whys = spans.flatMap((span) => (span.why === null ? [] : [span.why]));
  const [first] = whys;
  if (first === undefined) return "";
  const facts: Facts = { ...first.params };
  for (const [key, value] of Object.entries(first.params)) {
    // A list, such as the letters left out, is every place's together, each once.
    if (Array.isArray(value)) facts[key] = [...new Set(whys.flatMap((why) => listIn(why.params[key])))];
  }
  return fill(copy.approximate[first.code] ?? "", facts);
}

/** A fact as a list: itself if it is one, else nothing. */
function listIn(fact: Facts[string] | undefined): readonly string[] {
  return Array.isArray(fact) ? fact : [];
}

/** The group under `key` in `groups`, made empty the first time. */
function groupOf<K, T>(groups: Map<K, T[]>, key: K): T[] {
  const group = groups.get(key) ?? [];
  groups.set(key, group);
  return group;
}

/** `rows` with the most places first. */
function mostFirst<T extends { places: number }>(rows: T[]): T[] {
  return rows.toSorted((a, b) => b.places - a.places);
}
