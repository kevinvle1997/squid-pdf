// The fit check as the user types, mirroring editing/fit.py with the server's thresholds and widths; where
// the two can't agree (a missing letter switches the font), the server's fit lands with the render and wins.
import type { FitRules, FontInfo, Sentences, SpanInfo, Strategy } from "../api/types";
import { MISSING_SPACE_EM } from "./constants";
import type { Drawing } from "./substitutes";
import { fill, shownAs } from "./words";

/** What the browser's fit check makes of a span reading other words. */
export interface Fit {
  readonly originalPt: number; // how wide the original is, in points: where a way out ends the line
  readonly deltaPt: number; // how much longer than the original, in points; negative is shorter
  readonly roomPt: number; // the free space after the span on its line: too long is only past it
  readonly missing: readonly string[]; // letters the span's font can't draw, each once, in the order typed
  readonly options: readonly Strategy[]; // the ways out of an overflow, when there is one
}

/** How a line is set to end where the original did: its size, and how much narrower its letters, as shares of its own. */
export interface LineScale {
  readonly size: number;
  readonly scaleX: number;
}

/** A line set as the span is: what's drawn when no way out is taken. */
const AS_SET: LineScale = { size: 1, scaleX: 1 };

/** How a span reading other words is drawn: the way out, the scale it sets the line at, and the ways to choose from. */
export interface Setting extends LineScale {
  readonly way: Strategy;
  readonly offered: readonly Strategy[];
}

const WHITESPACE = /\s/;

/** How wide `text` draws at `size`, from widths in thousandths of the size. */
export function widthPt(text: string, glyphs: Record<string, number>, size: number): number {
  let thousandths = 0;
  for (const letter of text) {
    const width = glyphs[letter];
    if (width !== undefined) thousandths += width;
    // pdfTeX's subsets have no space at all: the space is an offset, not a letter.
    else if (WHITESPACE.test(letter)) thousandths += MISSING_SPACE_EM * 1000;
  }
  return (thousandths * size) / 1000;
}

type SpacedBy = Pick<SpanInfo, "size" | "letter_spacing_em">;

/** How wide `text` is set as `span` sets its letters, in points: their widths, each letter spaced as the span's are. */
export function lineWidthPt(span: SpacedBy, text: string, glyphs: Record<string, number>): number {
  return widthPt(text, glyphs, span.size) + letterSpacingPt(span, text);
}

/** How much `span`'s letter spacing adds to `text`, in points. */
export function letterSpacingPt(span: SpacedBy, text: string): number {
  // Counted by letter, not UTF-16 unit, as the server counts them.
  return span.letter_spacing_em * span.size * [...text].length;
}

/**
 * `text` spelled as the font has it: as typed, composed or in pieces (é is one letter, or e and an
 * accent). With none whole in the font, composed, as the faces we ship draw it. `_spelled` in core/plan.py.
 */
function spelledIn(text: string, glyphs: Record<string, number>): string {
  const composed = text.normalize("NFC");
  const spellings = [text, composed, text.normalize("NFD")];
  return spellings.find((spelling) => lackedIn(spelling, glyphs).length === 0) ?? composed;
}

/**
 * The letters of `text`, as the font spells it, that `glyphs` doesn't have, each once: a space too, as the
 * server counts one, since `glyphs` has a plain space wherever the server draws it.
 */
export function missingIn(text: string, glyphs: Record<string, number>): string[] {
  return lackedIn(spelledIn(text, glyphs), glyphs);
}

/** The letters of `text` exactly as typed that `glyphs` doesn't have, each once. */
function lackedIn(text: string, glyphs: Record<string, number>): string[] {
  const missing = new Set<string>();
  for (const letter of text) {
    if (glyphs[letter] === undefined) missing.add(letter);
  }
  return [...missing];
}

/** Whether a line `deltaPt` longer runs past its room, or the tolerance if wider: `runs_past` in editing/fit.py. */
export function runsPast(deltaPt: number, roomPt: number, rules: FitRules): boolean {
  return deltaPt > Math.max(rules.tolerance_pt, roomPt + rules.room_slack_pt);
}

/** The ways out of an overflow, in the order worth trying; none when it fits in its room. Each ends where the original did. */
export function optionsFor(
  deltaPt: number,
  { originalPt, roomPt, rules }: { originalPt: number; roomPt: number; rules: FitRules },
): Strategy[] {
  if (!runsPast(deltaPt, roomPt, rules) || originalPt <= 0) return [];
  const shrunkTo = originalPt / (originalPt + deltaPt);
  const squeezedBy = deltaPt / originalPt;
  const options: Strategy[] = [];
  if (shrunkTo >= rules.shrink_floor) options.push("shrink");
  if (squeezedBy <= rules.condense_limit) options.push("condense");
  options.push("as-is");
  return options;
}

/** What would happen if `span` read `text` instead. */
export function fitOf(span: SpanInfo, { font, text, rules }: { font: FontInfo; text: string; rules: FitRules }): Fit {
  const spelled = spelledIn(text, font.glyphs);
  const originalPt = lineWidthPt(span, span.text, font.glyphs);
  const deltaPt = Math.round((lineWidthPt(span, spelled, font.glyphs) - originalPt) * 100) / 100;
  const roomPt = span.room_pt;
  return {
    originalPt,
    deltaPt,
    roomPt,
    missing: missingIn(spelled, font.glyphs),
    options: optionsFor(deltaPt, { originalPt, roomPt, rules }),
  };
}

/** The way out drawn: the one asked for if it's offered, else as typed. `_strategy_drawn` in editing/fit.py. */
export function wayDrawn(asked: Strategy | undefined, { options }: Pick<Fit, "options">): Strategy {
  return asked !== undefined && options.includes(asked) ? asked : "as-is";
}

/** What a way out sets the line at, to end where the original did. `replace_fit` in editing/fit.py. */
export function setAs(way: Strategy, { originalPt, deltaPt }: Pick<Fit, "originalPt" | "deltaPt">): LineScale {
  const toEnd = originalPt / (originalPt + deltaPt);
  switch (way) {
    case "shrink":
      return { size: toEnd, scaleX: 1 };
    case "condense":
      return { size: 1, scaleX: toEnd };
    case "as-is":
      return AS_SET;
    default:
      return way satisfies never;
  }
}

/** The ways out to choose from: none when leaving the line long is the only one. */
export function waysOffered({ options }: Pick<Fit, "options">): readonly Strategy[] {
  return options.length > 1 ? options : [];
}

/** How `span` reading `text` is drawn, the way out `asked` while it's offered; as typed if its font can't be measured. */
export function settingOf(
  span: SpanInfo,
  {
    font,
    text,
    asked,
    rules,
  }: { font: FontInfo | undefined; text: string; asked: Strategy | undefined; rules: FitRules },
): Setting {
  if (font === undefined) return { way: "as-is", ...AS_SET, offered: [] };
  const fit = fitOf(span, { font, text, rules });
  const way = wayDrawn(asked, fit);
  return { way, ...setAs(way, fit), offered: waysOffered(fit) };
}

// A line's width when it has none to speak of: what a share past its room is taken of. `_SOME_WIDTH_PT` in editing/fit.py.
const SOME_WIDTH_PT = 1;

/** How far past its room the line runs, as a whole percent of the original, at least 1: `percent_past_room` in editing/fit.py. */
export function percentPastRoom({
  originalPt,
  deltaPt,
  roomPt,
}: Pick<Fit, "originalPt" | "deltaPt" | "roomPt">): number {
  // Rounded first, so a share that's whole in decimal isn't pushed up by binary.
  const share = Math.round(((deltaPt - roomPt) / Math.max(originalPt, SOME_WIDTH_PT)) * 100 * 1e6) / 1e6;
  return Math.max(1, Math.ceil(share));
}

/** Everything that won't come out as typed, in the server's words and order (`FitReport.describe`); empty when it fits. */
export function troublesOf(
  fit: Fit,
  {
    rules,
    sentences,
    drawing,
    way = "as-is",
  }: { rules: FitRules; sentences: Sentences; drawing: Drawing; way?: Strategy },
): string[] {
  const troubles = letterTroublesOf(drawing, sentences);
  // The part past its room collides, unless a way out ends it where the original did.
  if (runsPast(fit.deltaPt, fit.roomPt, rules) && way === "as-is") {
    troubles.push(fill(sentences.too_long, { percent: String(percentPastRoom(fit)) }));
  }
  return troubles;
}

/** The letters that won't come out as typed, in the server's words: those that send the line to another face, then those left out. */
export function letterTroublesOf(drawing: Drawing, sentences: Sentences): string[] {
  const troubles: string[] = [];
  const named = (letters: readonly string[]) => letters.map((letter) => shownAs(letter, sentences.characters));
  // Letters its own font lacks but the face it goes to has: the whole line switches.
  if (drawing.switched.length > 0) {
    troubles.push(fill(sentences.missing, { chars: named(drawing.switched), font: drawing.face }));
  }
  // Letters nothing we have draws.
  if (drawing.leftOut.length > 0) troubles.push(fill(sentences.will_leave_out, { letters: named(drawing.leftOut) }));
  return troubles;
}

/**
 * What a part of the line, such as a calendar day's date, adds to its troubles if chosen, in the server's words:
 * `fit` is the line with it, `rest` the line without it and `now` the line as it stands, drawn the way `asked` while
 * it's offered. Briefly: the letters it needs that the span's font lacks, which the rest of the line doesn't already,
 * a letter being the part's own, so the one chosen is marked for it too; and its running the line past its room,
 * where the line now isn't said to, a length being the whole line's. What the rest gets wrong, or draws them
 * instead, is the slip's full verdict to say, above.
 */
export function troublesAddedOf(
  fit: Fit,
  {
    rest,
    now,
    asked,
    rules,
    sentences,
  }: { rest: Fit; now: Fit; asked: Strategy | undefined; rules: FitRules; sentences: Sentences },
): string[] {
  const troubles: string[] = [];
  const added = fit.missing.filter((letter) => !rest.missing.includes(letter));
  const named = added.map((letter) => shownAs(letter, sentences.characters));
  if (added.length > 0) troubles.push(fill(sentences.not_in_font, { chars: named }));
  // Too long as `troublesOf` says it. Once the line is, the verdict says so: no day says it again, though one runs
  // further. Why not against the rest: words typed elsewhere, shorter than the date, can run the line past its room
  // where the rest alone fits, and every day would say it, the one chosen too.
  const tooLong = (line: Fit) => runsPast(line.deltaPt, line.roomPt, rules) && wayDrawn(asked, line) === "as-is";
  if (tooLong(fit) && !tooLong(now)) {
    troubles.push(fill(sentences.too_long, { percent: String(percentPastRoom(fit)) }));
  }
  return troubles;
}
