// What one page draws from the editor's state, selected as parts so the page redraws only when one
// of them changes: compared with `shallowEqual`, each part stays the same object until it does.
import { pageUrl } from "../api/client";
import type { FitRules, Sentences, SpanInfo } from "../api/types";
import type { EditorState, FocusSpan, ScrollTo } from "./editor";
import type { FailedImage } from "./images";
import type { Match } from "./matching";
import type { PreviewDisagreement } from "./previewDisagreements";
import type { PageEdits } from "./project";
import type { PageFits, PageRedactions, Strip } from "./render";
import type { FontsByObject } from "./spanFonts";

const NO_SPANS: readonly SpanInfo[] = [];
const NO_FITS: PageFits = {};
const NO_REDACTIONS: PageRedactions = {};

/** Everything a page draws from the editor's state: it redraws when one of these changes. */
export interface PageState {
  readonly spans: readonly SpanInfo[]; // its text, in the document's order
  readonly edits: PageEdits | undefined; // what the history reads as, here
  readonly drawnFrom: PageEdits | undefined; // what its strips were drawn from
  readonly strips: readonly Strip[] | undefined;
  readonly fits: PageFits;
  readonly redactions: PageRedactions; // the server's verdict on each redaction drawn here
  readonly matches: ReadonlyMap<string, readonly Match[]> | undefined; // what find found here, by span, while it's open
  readonly foundAt: string | null; // the place find went to, when it's here
  readonly shownMismatches: ReadonlySet<string>; // the rows of the mismatch list shown, by key
  readonly changed: boolean; // whether anything in the document is changed: what those rows count
  readonly disagreement: PreviewDisagreement | undefined; // the spans its last render disagreed with, to pulse
  readonly src: string; // the page image
  readonly image: FailedImage | undefined; // why its image failed, and how often it's been asked for again
  readonly typingIn: string | null; // the span being typed into, when it's here
  readonly unredacting: string | null; // the redacted span asking before it's edited, when it's here
  readonly focusTo: FocusSpan | null; // the span focus is sent to, when it's here
  readonly scrollTo: ScrollTo | null; // the span brought into view, when it's here
  // What every mark shows, selected once here rather than by each mark.
  readonly fonts: FontsByObject;
  readonly sentences: Sentences;
  readonly rules: FitRules; // for the way out a preview is drawn in
}

/** What page `index` draws from `state`. */
export function pageState(state: EditorState, index: number): PageState {
  // What's sent to one span is this page's only while that span is on it.
  const here = <T extends { readonly spanId: string }>(sent: T | null): T | null =>
    sent !== null && state.layout.spans.get(sent.spanId)?.page === index ? sent : null;
  return {
    spans: state.layout.pages.get(index) ?? NO_SPANS,
    edits: state.reading.pages.get(index),
    drawnFrom: state.drawn.from.get(index)?.pages.get(index),
    strips: state.drawn.strips.get(index),
    fits: state.drawn.fits.get(index) ?? NO_FITS,
    redactions: state.drawn.redactions.get(index) ?? NO_REDACTIONS,
    matches: state.find.matches.get(index),
    foundAt:
      state.find.current !== null && state.layout.spans.get(state.find.current)?.page === index
        ? state.find.current
        : null,
    shownMismatches: state.shownMismatches,
    // Read only for the rows shown: with none, a first change elsewhere redraws no page.
    changed: state.shownMismatches.size > 0 && state.reading.spans.size > 0,
    disagreement: state.disagreements.get(index),
    src: pageUrl(state.doc, index, state.scale),
    image: state.images.get(index),
    typingIn: state.draft?.page === index ? state.draft.spanId : null,
    unredacting: here(state.unredacting)?.spanId ?? null,
    focusTo: here(state.focusTo !== null && "spanId" in state.focusTo ? state.focusTo : null),
    scrollTo: here(state.scrollTo),
    fonts: state.layout.fonts,
    sentences: state.doc.sentences,
    rules: state.doc.fit,
  };
}

/** The span `spanId` among the page's, if it's there. */
export function spanIn(page: PageState, spanId: string): SpanInfo | undefined {
  return page.spans.find((span) => span.id === spanId);
}
