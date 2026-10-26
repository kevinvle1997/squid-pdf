// In the settle, the spans whose render disagrees with their preview pulse (decisions/frontend.md, Motion).
// Verdicts are compared, not pixels: the preview draws in a face we ship.
import type { FitInfo, FitRules, Strategy } from "../api/types";
import { type Fit, fitOf, runsPast } from "./fit";
import { spanOf } from "./history";
import { type Differing, differing, type Reading } from "./project";
import type { Drawn } from "./render";
import { type FontsByObject, fontOf } from "./spanFonts";

/** The spans on one page whose render disagreed with their preview, as one render landed. */
export interface PreviewDisagreement {
  readonly spanIds: ReadonlySet<string>;
  readonly count: number; // which landing it pulses for, so a span that disagrees again pulses again
}

export const NO_DISAGREEMENTS: ReadonlyMap<number, PreviewDisagreement> = new Map();

interface Judged {
  fonts: FontsByObject;
  rules: FitRules;
  disagreements: ReadonlyMap<number, PreviewDisagreement>; // what pulsed before, kept for the pages this landing didn't draw
}

/** What pulses once `after` lands over `before`: on each page it drew, the previewed spans its render disagrees with. */
export function disagreementsAfter(
  before: Drawn,
  after: Drawn,
  { fonts, rules, disagreements }: Judged,
): ReadonlyMap<number, PreviewDisagreement> {
  let next: Map<number, PreviewDisagreement> | null = null;
  for (const [page, reading] of after.from) {
    const was = before.from.get(page);
    // Not drawn by this landing: whatever it showed stays.
    if (was === reading) continue;
    const previewed = differing(reading.pages.get(page), was?.pages.get(page));
    const landed = { reading, page, after, fonts, rules };
    const spanIds = new Set(previewed.filter((each) => disagrees(each, landed)).map(({ span }) => span.id));
    next ??= new Map(disagreements);
    if (spanIds.size === 0) next.delete(page);
    else next.set(page, { spanIds, count: (disagreements.get(page)?.count ?? 0) + 1 });
  }
  return next ?? disagreements;
}

/** One page of a landing: what it drew, and what the live check needs to judge it. */
interface PageLanded {
  readonly reading: Reading; // what the landing drew
  readonly page: number;
  readonly after: Drawn;
  readonly fonts: FontsByObject;
  readonly rules: FitRules;
}

/** Whether the render of a previewed span says something the browser's live check didn't. */
function disagrees({ span, text, redacted }: Differing, { reading, page, after, fonts, rules }: PageLanded): boolean {
  // A redaction previews bare, as it's drawn: there's no verdict to differ from.
  if (redacted) return false;
  // The browser never foresees a notice or a skip.
  const noticed = after.notices.get(page)?.some((notice) => notice.kind === "span" && notice.span_id === span.id);
  const skipped = after.skipped.some(({ edit }) => {
    const sent = reading.edits[edit];
    return sent !== undefined && spanOf(sent) === span.id;
  });
  if (noticed || skipped) return true;
  const fit = after.fits.get(page)?.[span.id];
  const font = fontOf(fonts, span);
  // Put back, or a font the browser can't measure: nothing was foreseen.
  if (fit === undefined || font === undefined) return false;
  return !sameVerdict(fit, {
    live: fitOf(span, { font, text, rules }),
    asked: reading.spans.get(span.id)?.strategy,
    rules,
  });
}

/** Whether the server's fit says what the live check did: the same letters lacking, as long, the way out asked. */
function sameVerdict(
  fit: FitInfo,
  { live, asked, rules }: { live: Fit; asked: Strategy | undefined; rules: FitRules },
): boolean {
  const lacking = new Set([...fit.missing, ...fit.left_out]);
  const sameLetters = lacking.size === live.missing.length && live.missing.every((letter) => lacking.has(letter));
  const sameLength = runsPast(fit.delta_pt, live.roomPt, rules) === runsPast(live.deltaPt, live.roomPt, rules);
  const sameWayOut = asked === undefined || fit.strategy === asked;
  return sameLetters && sameLength && sameWayOut;
}
