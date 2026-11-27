// Mirrors editing/fit.py; where the two can't agree (a missing letter switches the font), the server's fit lands with the render and wins.
import type { FitRules, FontInfo, Sentences, SpanInfo, Strategy } from "../api/types";
import { MISSING_SPACE_EM } from "./constants";
import type { Drawing } from "./substitutes";
import { fill, shownAs } from "./words";

export interface Fit {
  readonly originalPt: number;
  readonly deltaPt: number;
  readonly roomPt: number;
  readonly missing: readonly string[];
  readonly options: readonly Strategy[];
}

export interface LineScale {
  readonly size: number;
  readonly scaleX: number;
}

const AS_SET: LineScale = { size: 1, scaleX: 1 };

export interface Setting extends LineScale {
  readonly way: Strategy;
  readonly offered: readonly Strategy[];
}

const WHITESPACE = /\s/;

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

export function lineWidthPt(span: SpacedBy, text: string, glyphs: Record<string, number>): number {
  return widthPt(text, glyphs, span.size) + letterSpacingPt(span, text);
}

export function letterSpacingPt(span: SpacedBy, text: string): number {
  // Counted by letter, not UTF-16 unit, as the server counts them.
  return span.letter_spacing_em * span.size * [...text].length;
}

function spelledIn(text: string, glyphs: Record<string, number>): string {
  const composed = text.normalize("NFC");
  const spellings = [text, composed, text.normalize("NFD")];
  return spellings.find((spelling) => lackedIn(spelling, glyphs).length === 0) ?? composed;
}

/** A space counts too: `glyphs` has a plain space wherever the server draws one. */
export function missingIn(text: string, glyphs: Record<string, number>): string[] {
  return lackedIn(spelledIn(text, glyphs), glyphs);
}

function lackedIn(text: string, glyphs: Record<string, number>): string[] {
  const missing = new Set<string>();
  for (const letter of text) {
    if (glyphs[letter] === undefined) missing.add(letter);
  }
  return [...missing];
}

export function runsPast(deltaPt: number, roomPt: number, rules: FitRules): boolean {
  return deltaPt > Math.max(rules.tolerance_pt, roomPt + rules.room_slack_pt);
}

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

/** A run of a line as typed: its span, the font that sets it, and the words it reads. */
export interface TypedRun {
  readonly span: SpanInfo;
  readonly font: FontInfo;
  readonly text: string;
}

/** One run of a line, measured: where the page starts it, its own words' width, and the typed words'. */
export interface RunWidths {
  readonly x: number;
  readonly originalPt: number;
  readonly typedPt: number;
  readonly changed: boolean;
}

/**
 * A line's runs fitted by those that change: the line's fit, the way out drawn, how wide the changed runs are drawn
 * as a share of their typed width, and where each run starts.
 */
export interface RunsLaidOut {
  readonly fit: Fit; // the line's, no one run's letters
  readonly way: Strategy;
  readonly scale: number;
  readonly starts: readonly number[];
}

/**
 * A line's runs as typed, fitted in its room by those that change, and set along it: `runs_laid_out` in
 * editing/fit.py, held to `line_cases` in tests/editing/fit_cases.json. A way out scales only the changed runs, to
 * where they ended, so nothing after them moves; as typed, the runs after a change move with it.
 */
export function runsLaidOut(
  runs: readonly RunWidths[],
  { roomPt, asked, rules }: { roomPt: number; asked: Strategy | undefined; rules: FitRules },
): RunsLaidOut {
  const changed = runs.filter((run) => run.changed);
  const originalPt = sumOf(changed.map((run) => run.originalPt));
  const deltaPt = sumOf(changed.map((run) => run.typedPt)) - originalPt;
  const options = optionsFor(deltaPt, { originalPt, roomPt, rules });
  const way = wayDrawn(asked, { options });
  // A way out is offered only past the room, so the changed runs have some width then.
  const scale = way === "as-is" ? 1 : originalPt / (originalPt + deltaPt);
  const fit = { originalPt, deltaPt: Math.round(deltaPt * 100) / 100, roomPt, missing: [], options };
  return { fit, way, scale, starts: runStartsOf(runs, scale) };
}

/** Where each run starts once those before it are drawn, changed ones `scale` wide, each gap the page left kept. */
function runStartsOf(runs: readonly RunWidths[], scale: number): number[] {
  const starts: number[] = [];
  let nextPt = runs[0]?.x ?? 0;
  for (const [index, run] of runs.entries()) {
    const before = runs[index - 1];
    if (before !== undefined) nextPt += run.x - (before.x + before.originalPt);
    starts.push(nextPt);
    nextPt += run.changed ? run.typedPt * scale : run.typedPt;
  }
  return starts;
}

/** A run of a line as typed: its span, the font that sets it, and the words it reads. */
export interface TypedRun {
  readonly span: SpanInfo;
  readonly font: FontInfo;
  readonly text: string;
}

/** `runs` of a line measured, each from where the page starts it. */
export function runWidthsOf(runs: readonly TypedRun[]): RunWidths[] {
  return runs.map(({ span, font, text }) => {
    const originalPt = lineWidthPt(span, span.text, font.glyphs);
    const changed = text !== span.text;
    const typedPt = changed ? lineWidthPt(span, spelledIn(text, font.glyphs), font.glyphs) : originalPt;
    return { x: span.origin[0] ?? span.bbox.x0, originalPt, typedPt, changed };
  });
}

/**
 * What would happen if a line's runs read `runs`' words: the runs changed, set against the line's room, as one, and
 * every letter a changed run's font lacks.
 */
export function lineFitOf(runs: readonly TypedRun[], { roomPt, rules }: { roomPt: number; rules: FitRules }): Fit {
  const { fit } = runsLaidOut(runWidthsOf(runs), { roomPt, asked: undefined, rules });
  const changed = runs.filter(({ span, text }) => text !== span.text);
  const missing = new Set(changed.flatMap(({ font, text }) => missingIn(text, font.glyphs)));
  return { ...fit, missing: [...missing] };
}

/** The sum of `widths`. */
function sumOf(widths: readonly number[]): number {
  return widths.reduce((total, width) => total + width, 0);
}

/** The way out drawn: the one asked for if it's offered, else as typed. `_strategy_drawn` in editing/fit.py. */
export function wayDrawn(asked: Strategy | undefined, { options }: Pick<Fit, "options">): Strategy {
  return asked !== undefined && options.includes(asked) ? asked : "as-is";
}

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

export function waysOffered({ options }: Pick<Fit, "options">): readonly Strategy[] {
  return options.length > 1 ? options : [];
}

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

const SOME_WIDTH_PT = 1;

export function percentPastRoom({
  originalPt,
  deltaPt,
  roomPt,
}: Pick<Fit, "originalPt" | "deltaPt" | "roomPt">): number {
  // Rounded first, so a share that's whole in decimal isn't pushed up by binary.
  const share = Math.round(((deltaPt - roomPt) / Math.max(originalPt, SOME_WIDTH_PT)) * 100 * 1e6) / 1e6;
  return Math.max(1, Math.ceil(share));
}

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
  if (runsPast(fit.deltaPt, fit.roomPt, rules) && way === "as-is") {
    troubles.push(fill(sentences.too_long, { percent: String(percentPastRoom(fit)) }));
  }
  return troubles;
}

export function letterTroublesOf(drawing: Drawing, sentences: Sentences): string[] {
  const troubles: string[] = [];
  const named = (letters: readonly string[]) => letters.map((letter) => shownAs(letter, sentences.characters));
  if (drawing.switched.length > 0) {
    troubles.push(fill(sentences.missing, { chars: named(drawing.switched), font: drawing.face }));
  }
  if (drawing.leftOut.length > 0) troubles.push(fill(sentences.will_leave_out, { letters: named(drawing.leftOut) }));
  return troubles;
}

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
  // Not against the rest: words typed elsewhere can run the line past its room where the rest alone fits, and every day would say it.
  const tooLong = (line: Fit) => runsPast(line.deltaPt, line.roomPt, rules) && wayDrawn(asked, line) === "as-is";
  if (tooLong(fit) && !tooLong(now)) {
    troubles.push(fill(sentences.too_long, { percent: String(percentPastRoom(fit)) }));
  }
  return troubles;
}
