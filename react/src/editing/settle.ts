// The settle: the server's render replacing the browser's preview. A render that says what the
// preview did swaps in silently; one that says otherwise pulses (decisions/frontend.md, Motion).
// What's compared is the verdict, not the pixels: the preview draws in a face we ship.
import type { FitInfo, FitRules, FontInfo, Strategy } from "../api/types";
import { type Fit, fitOf, runsPast } from "./fit";
import { spanOf } from "./history";
import { type Differing, differing, type Reading } from "./project";
import type { Drawn } from "./render";

/** The spans on one page whose render said other than their preview, as one render landed. */
export interface Settling {
  readonly spanIds: ReadonlySet<string>;
  readonly count: number; // which landing it pulses for, so a span that disagrees again pulses again
}

export const NOTHING_SETTLING: ReadonlyMap<number, Settling> = new Map();

interface Judged {
  fonts: ReadonlyMap<string, FontInfo>;
  rules: FitRules;
  settling: ReadonlyMap<number, Settling>; // what pulsed before, kept for the pages this landing didn't draw
}

/** What pulses once `after` lands over `before`: on each page it drew, the previewed spans its render disagrees with. */
export function settlingAfter(
  before: Drawn,
  after: Drawn,
  { fonts, rules, settling }: Judged,
): ReadonlyMap<number, Settling> {
  let next: Map<number, Settling> | null = null;
  for (const [page, reading] of after.from) {
    const was = before.from.get(page);
    // Not drawn by this landing: whatever it showed stays.
    if (was === reading) continue;
    const previewed = differing(reading.pages.get(page), was?.pages.get(page));
    const landed = { reading, page, after, fonts, rules };
    const spanIds = new Set(previewed.filter((each) => disagrees(each, landed)).map(({ span }) => span.id));
    next ??= new Map(settling);
    if (spanIds.size === 0) next.delete(page);
    else next.set(page, { spanIds, count: (settling.get(page)?.count ?? 0) + 1 });
  }
  return next ?? settling;
}

/** One page of a landing: what it drew, and what the live check needs to judge it. */
interface PageLanded {
  readonly reading: Reading; // what the landing drew
  readonly page: number;
  readonly after: Drawn;
  readonly fonts: ReadonlyMap<string, FontInfo>;
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
  const font = fonts.get(span.font);
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
