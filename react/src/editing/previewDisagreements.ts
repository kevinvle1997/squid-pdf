// Verdicts are compared, not pixels: the preview draws in a face we ship.

import type { FitInfo, FitRules, FontInfo, Strategy } from "../api/types";
import type { Layout } from "./editor";
import { type Fit, fitOf, lineFitOf, missingIn, runsPast } from "./fit";
import { spansOf } from "./history";
import { rulesFor } from "./lines";
import { newTextDrawingOf, newTextVerdictOf } from "./newText";
import { type Differing, differing, type NewTextReading, type Reading } from "./project";
import type { Drawn } from "./render";
import { type FontsByObject, fontOf } from "./spanFonts";
import { liveVerdictOf, type Verdict } from "./verdicts";

export interface PreviewDisagreement {
  readonly spanIds: ReadonlySet<string>;
  readonly newTextIds: ReadonlySet<number>;
  readonly count: number;
}

export const NO_DISAGREEMENTS: ReadonlyMap<number, PreviewDisagreement> = new Map();

interface Judged {
  layout: Pick<Layout, "fonts" | "spans" | "lines">;
  rules: FitRules;
  disagreements: ReadonlyMap<number, PreviewDisagreement>;
}

export function disagreementsAfter(
  before: Drawn,
  after: Drawn,
  { layout, rules, disagreements }: Judged,
): ReadonlyMap<number, PreviewDisagreement> {
  let next: Map<number, PreviewDisagreement> | null = null;
  for (const [page, reading] of after.from) {
    const was = before.from.get(page);
    if (was === reading) continue;
    const previewed = differing(reading.pages.get(page), was?.pages.get(page));
    const landed = { reading, page, after, fonts: layout.fonts, lines: layout.lines, rules };
    const spanIds = new Set(previewed.filter((each) => disagrees(each, landed)).map(({ span }) => span.id));
    const drawnBefore = new Set(was?.pages.get(page)?.newTexts.map((text) => text.id));
    const textsPreviewed = (reading.pages.get(page)?.newTexts ?? []).filter((text) => !drawnBefore.has(text.id));
    const texts = textsPreviewed.filter((text) => newTextDisagrees(text, { ...landed, layout }));
    const newTextIds = new Set(texts.map((text) => text.newTextId));
    next ??= new Map(disagreements);
    if (spanIds.size === 0 && newTextIds.size === 0) next.delete(page);
    else next.set(page, { spanIds, newTextIds, count: (disagreements.get(page)?.count ?? 0) + 1 });
  }
  return next ?? disagreements;
}

interface PageLanded {
  readonly reading: Reading;
  readonly page: number;
  readonly after: Drawn;
  readonly fonts: FontsByObject;
  readonly lines: Layout["lines"];
  readonly rules: FitRules;
}

function disagrees(each: Differing, landed: PageLanded): boolean {
  const { span, text, redacted } = each;
  const { reading, page, after, fonts, rules } = landed;
  if (redacted) return false;
  const noticed = after.notices.get(page)?.some((notice) => notice.kind === "span" && notice.span_id === span.id);
  const skipped = after.skipped.some(({ edit }) => {
    const sent = reading.edits[edit];
    return sent !== undefined && spansOf(sent).includes(span.id);
  });
  if (noticed || skipped) return true;
  const fit = after.fits.get(page)?.[span.id];
  const font = fontOf(fonts, span);
  if (fit === undefined || font === undefined) return false;
  return !sameVerdict(fit, {
    live: liveFitOf(each, { ...landed, font }),
    liveVerdict: liveVerdictOf(span, { font, text }),
    asked: reading.spans.get(span.id)?.strategy,
    rules: rulesFor(landed.lines.get(span.id), rules),
  });
}

function liveFitOf(
  { span, text, lineEdit }: Differing,
  { fonts, lines, rules, font }: PageLanded & { font: FontInfo },
): Fit {
  const line = lines.get(span.id);
  if (lineEdit === undefined || line === undefined) return fitOf(span, { font, text, rules });
  const runs = line.spans.flatMap((each, index) => {
    const runFont = fontOf(fonts, each);
    return runFont === undefined ? [] : [{ span: each, font: runFont, text: lineEdit.runs[index]?.text ?? each.text }];
  });
  const fit = lineFitOf(runs, { roomPt: line.roomPt, rules: rulesFor(line, rules) });
  return { ...fit, missing: text === span.text ? [] : missingIn(text, font.glyphs) };
}

function newTextDisagrees(
  text: NewTextReading,
  { reading, page, after, layout }: PageLanded & { layout: Pick<Layout, "fonts" | "spans" | "lines"> },
): boolean {
  const sentAt = reading.edits.indexOf(text.edit);
  const noticed = after.notices.get(page)?.some((notice) => notice.kind === "insert" && notice.edit === sentAt);
  const skipped = after.skipped.some(({ edit }) => edit === sentAt);
  if (noticed || skipped) return true;
  const fit = after.newTextFits.get(page)?.[text.id];
  if (fit === undefined) return false;
  const drawing = newTextDrawingOf(layout, text.edit);
  const live = new Set([...drawing.switched, ...drawing.leftOut]);
  const lacking = new Set([...fit.missing, ...fit.left_out]);
  const sameLetters = lacking.size === live.size && [...live].every((letter) => lacking.has(letter));
  return !sameLetters || fit.fidelity !== newTextVerdictOf(layout, { insert: text.edit, fit: undefined }).fidelity;
}

function sameVerdict(
  fit: FitInfo,
  {
    live,
    liveVerdict,
    asked,
    rules,
  }: { live: Fit; liveVerdict: Verdict; asked: Strategy | undefined; rules: FitRules },
): boolean {
  const lacking = new Set([...fit.missing, ...fit.left_out]);
  const sameLetters = lacking.size === live.missing.length && live.missing.every((letter) => lacking.has(letter));
  const sameLength = runsPast(fit.delta_pt, live.roomPt, rules) === runsPast(live.deltaPt, live.roomPt, rules);
  const sameWayOut = asked === undefined || fit.strategy === asked;
  const sameKind = fit.fidelity === liveVerdict.fidelity;
  return sameLetters && sameLength && sameWayOut && sameKind;
}
