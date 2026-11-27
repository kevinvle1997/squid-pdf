import type { FitInfo, FontInfo, Sentences, SpanInfo } from "../api/types";
import type { SpanReading } from "./project";
import type { PageFits } from "./render";
import { type FontsByObject, fontOf, shownNameIn } from "./spanFonts";
import { substituteOf } from "./substitutes";
import { isDrawnAgain, liveVerdictOf, type Verdict, verdictOf } from "./verdicts";
import { type Facts, fill, shownAs } from "./words";

export interface MismatchPlace {
  readonly span: SpanInfo;
  readonly verdict: Verdict;
  readonly missing: readonly string[];
}

export interface MismatchRow {
  readonly key: string;
}

export interface MismatchFont extends MismatchRow {
  readonly name: string;
  readonly places: number;
  readonly sentence: string;
}

export interface MismatchReason extends MismatchRow {
  readonly code: NonNullable<SpanInfo["why"]>["code"];
  readonly places: number;
  readonly sentence: string;
}

export interface Mismatches {
  readonly count: number;
  readonly fonts: readonly MismatchFont[];
  readonly reasons: readonly MismatchReason[];
}

export function mismatchesOf(
  places: readonly MismatchPlace[],
  { fonts, sentences }: { fonts: FontsByObject; sentences: Sentences },
): Mismatches {
  const byFont = new Map<string, MismatchPlace[]>();
  const byReason = new Map<MismatchReason["code"], Verdict[]>();
  for (const place of places) {
    const reason = reasonOf(place);
    if (reason === null) groupOf(byFont, shownNameIn(fonts, place.span)).push(place);
    else groupOf(byReason, reason).push(place.verdict);
  }
  const fontRows = [...byFont].map(([name, inFont]) => ({
    key: fontKey(name),
    name,
    places: inFont.length,
    sentence: fontSentence(inFont, { fonts, sentences }),
  }));
  const reasonRows = [...byReason].map(([code, verdicts]) => ({
    key: reasonKey(code),
    code,
    places: verdicts.length,
    sentence: reasonSentence(verdicts, sentences),
  }));
  return { count: places.length, fonts: mostFirst(fontRows), reasons: mostFirst(reasonRows) };
}

export function mismatchKeyOf(place: MismatchPlace, fonts: FontsByObject): string {
  const reason = reasonOf(place);
  return reason === null ? fontKey(shownNameIn(fonts, place.span)) : reasonKey(reason);
}

/**
 * `span` as a place an edit won't match, as the bar counts places now; none where it will match. Before
 * any change, every such span in the document, by its own verdict; after, only those drawn again, by their edit's
 * (`verdictOf`): `fit` is the server's once a render of it as it reads now landed, and its letters missing.
 */
export function placeOf(
  span: SpanInfo,
  {
    changed,
    reading,
    fit,
    font,
  }: { changed: boolean; reading: SpanReading | undefined; fit: FitInfo | undefined; font: FontInfo | undefined },
): MismatchPlace | undefined {
  if (!changed) return span.fidelity === "exact" ? undefined : { span, verdict: span, missing: [] };
  if (reading === undefined || !isDrawnAgain(reading, fit)) return undefined;
  const live = () => liveVerdictOf(span, { font, text: reading.text });
  const verdict = verdictOf(span, { reading, fit, live });
  return verdict.fidelity === "exact" ? undefined : { span, verdict, missing: fit?.missing ?? [] };
}

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
    fonts: FontsByObject;
  },
): SpanInfo[] {
  if (shown.size === 0) return [];
  return spans.filter((span) => {
    const facts = { changed, reading: readings.get(span.id), fit: fits[span.id], font: fontOf(fonts, span) };
    const place = placeOf(span, facts);
    return place !== undefined && shown.has(mismatchKeyOf(place, fonts));
  });
}

function reasonOf({ verdict }: MismatchPlace): MismatchReason["code"] | null {
  // Drawn in a similar font: whatever else won't match, its font is what the reader can change.
  return verdict.fidelity === "approximate" && verdict.why !== null ? verdict.why.code : null;
}

function fontKey(name: string): string {
  return `font:${name}`;
}

function reasonKey(code: MismatchReason["code"]): string {
  return `reason:${code}`;
}

function fontSentence(
  places: MismatchPlace[],
  { fonts, sentences }: { fonts: FontsByObject; sentences: Sentences },
): string {
  const [first] = places;
  if (first === undefined) return "";
  const font = fontOf(fonts, first.span);
  const chars = [...new Set(places.flatMap((place) => place.missing))].map((letter) =>
    shownAs(letter, sentences.characters),
  );
  if (font === undefined) return fill(sentences.missing, { chars, font: first.verdict.substitute ?? "" });
  if (font.why !== null) return font.why;
  const substitute = substituteOf(first.span, font);
  if (chars.length > 0) return fill(sentences.missing, { chars, font: substitute });
  return fill(font.same_widths ? sentences.substitute_same_widths : sentences.substitute, { font: substitute });
}

function reasonSentence(verdicts: Verdict[], sentences: Sentences): string {
  const whys = verdicts.flatMap((verdict) => (verdict.why === null ? [] : [verdict.why]));
  const [first] = whys;
  if (first === undefined) return "";
  const facts: Facts = { ...first.params };
  for (const [key, value] of Object.entries(first.params)) {
    if (Array.isArray(value)) facts[key] = [...new Set(whys.flatMap((why) => listIn(why.params[key])))];
  }
  return fill(sentences.approximate[first.code] ?? "", facts);
}

function listIn(fact: Facts[string] | undefined): readonly string[] {
  return Array.isArray(fact) ? fact : [];
}

function groupOf<K, T>(groups: Map<K, T[]>, key: K): T[] {
  const group = groups.get(key) ?? [];
  groups.set(key, group);
  return group;
}

function mostFirst<T extends { places: number }>(rows: T[]): T[] {
  return rows.toSorted((a, b) => b.places - a.places);
}
