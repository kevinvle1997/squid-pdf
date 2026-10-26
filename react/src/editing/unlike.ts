// Where an edit won't match the text around it, grouped by why: each font a similar one draws for,
// then each reason the file's own font won't match. The bar's count opens it (quality.md, Rule 1).
import type { FontInfo, Sentences, SpanInfo } from "../api/types";
import { wontMatch } from "./marks";
import type { SpanReading } from "./project";
import type { PageFits } from "./render";
import { substituteOf } from "./substitutes";
import { type Facts, fill, shownAs } from "./words";

/** A span an edit won't match at, and the letters its own font lacks for the words typed there, if those are why. */
export interface UnlikePlace {
  readonly span: SpanInfo;
  readonly missing: readonly string[];
}

/** A row of the list, as the page knows it: which group, by its key. */
export interface UnlikeRow {
  readonly key: string;
}

/** One font a similar one draws for: its name as shown, how many places, and why, in the server's words. */
export interface UnlikeFont extends UnlikeRow {
  readonly name: string;
  readonly places: number;
  readonly said: string;
}

/** One reason the file's own font won't match the text around it: how many places, and the server's sentence. */
export interface UnlikeReason extends UnlikeRow {
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
  { fonts, sentences }: { fonts: ReadonlyMap<string, FontInfo>; sentences: Sentences },
): Unlike {
  const byFont = new Map<string, UnlikePlace[]>();
  const byReason = new Map<UnlikeReason["code"], SpanInfo[]>();
  for (const place of places) {
    const reason = reasonOf(place);
    if (reason === null) groupOf(byFont, shownNameIn(fonts, place.span)).push(place);
    else groupOf(byReason, reason).push(place.span);
  }
  const fontRows = [...byFont].map(([name, inFont]) => ({
    key: fontKey(name),
    name,
    places: inFont.length,
    said: fontSaid(inFont, { fonts, sentences }),
  }));
  const reasonRows = [...byReason].map(([code, spans]) => ({
    key: reasonKey(code),
    code,
    places: spans.length,
    said: reasonSaid(spans, sentences),
  }));
  return { count: places.length, fonts: mostFirst(fontRows), reasons: mostFirst(reasonRows) };
}

/** The row `place` is in, by its key: its font's when a similar one draws it, else why its own won't match. */
export function unlikeKeyOf(place: UnlikePlace, fonts: ReadonlyMap<string, FontInfo>): string {
  const reason = reasonOf(place);
  return reason === null ? fontKey(shownNameIn(fonts, place.span)) : reasonKey(reason);
}

/**
 * `span` as a place an edit won't match, as the bar counts places now; none where it will match.
 * Before any change, every such span in the document; after, only those changed, letters the server
 * found missing included.
 */
export function placeOf(
  span: SpanInfo,
  { changed, reading, missing }: { changed: boolean; reading: SpanReading | undefined; missing: readonly string[] },
): UnlikePlace | undefined {
  // Before any change: the whole document, as the bar's count is.
  if (!changed) return wontMatch(span) ? { span, missing: [] } : undefined;
  const replaced = reading?.replaced ?? false;
  return replaced && (wontMatch(span) || missing.length > 0) ? { span, missing } : undefined;
}

/** The spans of `spans` in a row that's shown, as the list counts places now. */
export function shownAmong(
  spans: readonly SpanInfo[],
  {
    shown,
    changed,
    readings,
    fits,
    fonts,
  }: {
    shown: ReadonlySet<string>;
    changed: boolean;
    readings: ReadonlyMap<string, SpanReading>;
    fits: PageFits;
    fonts: ReadonlyMap<string, FontInfo>;
  },
): SpanInfo[] {
  // Nothing shown: no span need be judged.
  if (shown.size === 0) return [];
  return spans.filter((span) => {
    const missing = fits[span.id]?.missing ?? [];
    const place = placeOf(span, { changed, reading: readings.get(span.id), missing });
    return place !== undefined && shown.has(unlikeKeyOf(place, fonts));
  });
}

/** Why the file's own font won't match at `place`, when that's its row; null when a similar font draws it. */
function reasonOf({ span, missing }: UnlikePlace): UnlikeReason["code"] | null {
  // Drawn in a similar font: whatever else is unlike, its font is what the reader can change.
  if (span.substitute !== null || missing.length > 0 || span.why === null) return null;
  return span.why.code;
}

/** The name the reader knows `span`'s font by. */
function shownNameIn(fonts: ReadonlyMap<string, FontInfo>, span: SpanInfo): string {
  return fonts.get(span.font)?.shown_name ?? span.font;
}

/** The key of the row for the font the reader knows as `name`. */
function fontKey(name: string): string {
  return `font:${name}`;
}

/** The key of the row for the reason `code`. */
function reasonKey(code: UnlikeReason["code"]): string {
  return `reason:${code}`;
}

/** Why a similar font draws a font's places: the font's own reason, else the letters it lacks, else what draws it. */
function fontSaid(
  places: UnlikePlace[],
  { fonts, sentences }: { fonts: ReadonlyMap<string, FontInfo>; sentences: Sentences },
): string {
  const [first] = places;
  const font = first === undefined ? undefined : fonts.get(first.span.font);
  if (first === undefined || font === undefined) return "";
  if (font.why !== null) return font.why;
  const substitute = substituteOf(first.span, font);
  const chars = [...new Set(places.flatMap((place) => place.missing))].map((letter) =>
    shownAs(letter, sentences.characters),
  );
  if (chars.length > 0) return fill(sentences.missing, { chars, font: substitute });
  return fill(font.same_widths ? sentences.substitute_same_widths : sentences.substitute, { font: substitute });
}

/** A reason's sentence, its facts gathered from every place: the letters no font can draw at any of them. */
function reasonSaid(spans: SpanInfo[], sentences: Sentences): string {
  const whys = spans.flatMap((span) => (span.why === null ? [] : [span.why]));
  const [first] = whys;
  if (first === undefined) return "";
  const facts: Facts = { ...first.params };
  for (const [key, value] of Object.entries(first.params)) {
    // A list, such as the letters left out, is every place's together, each once.
    if (Array.isArray(value)) facts[key] = [...new Set(whys.flatMap((why) => listIn(why.params[key])))];
  }
  return fill(sentences.approximate[first.code] ?? "", facts);
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
