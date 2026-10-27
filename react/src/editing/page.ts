// What one page draws from the editor's state, selected as parts so the page redraws only when one
// of them changes: compared with `shallowEqual`, each part stays the same object until it does.
import { pageUrl } from "../api/client";
import type { FitRules, Sentences, SpanInfo } from "../api/types";
import { NEW_TEXT_ASCENT_EM } from "./constants";
import { changedWordsCount, type EditorState, type FocusSpan, type Layout, type ScrollTo } from "./editor";
import type { FailedImage } from "./images";
import type { Match } from "./matching";
import { keptNewTexts, type NewTextDraft, type PagePoint } from "./newText";
import type { PreviewDisagreement } from "./previewDisagreements";
import type { NewTextReading, PageEdits } from "./project";
import type { PageFits, PageNewTextFits, PageRedactions, Strip } from "./render";
import type { FontsByObject } from "./spanFonts";

const NO_SPANS: readonly SpanInfo[] = [];
const NO_FITS: PageFits = {};
const NO_NEW_TEXT_FITS: PageNewTextFits = {};
const NO_REDACTIONS: PageRedactions = {};

/** Everything a page draws from the editor's state: it redraws when one of these changes. */
export interface PageState {
  readonly spans: readonly SpanInfo[]; // its text, in the document's order
  readonly edits: PageEdits | undefined; // what the history reads as, here
  readonly drawnFrom: PageEdits | undefined; // what its strips were drawn from
  readonly strips: readonly Strip[] | undefined;
  readonly fits: PageFits;
  readonly newTextFits: PageNewTextFits; // the server's fit for each piece of new text here, by its version
  readonly redactions: PageRedactions; // the server's verdict on each redaction drawn here
  readonly matches: ReadonlyMap<string, readonly Match[]> | undefined; // what find found here, by span, while it's open
  readonly foundAt: string | null; // the place find went to, when it's here
  readonly shownMismatches: ReadonlySet<string>; // the rows of the mismatch list shown, by key
  readonly changed: boolean; // whether any words the download keeps are changed: what those rows count
  readonly disagreement: PreviewDisagreement | undefined; // the spans its last render disagreed with, to pulse
  readonly src: string; // the page image
  readonly image: FailedImage | undefined; // why its image failed, and how often it's been asked for again
  readonly typingIn: string | null; // the span being typed into, when it's here
  readonly newTextAt: PagePoint | null; // where new text is being typed, when it's here
  readonly newTextTypedAgain: number | null; // the new text typed into again, if that's what's typed here
  readonly unredacting: string | null; // the redacted span asking before it's edited, when it's here
  readonly focusTo: FocusSpan | null; // the span focus is sent to, when it's here
  readonly focusNewText: number | null; // the new text focus is sent to, when it's here
  readonly howToStart: boolean; // the first page, before any edit: its margin says how to start
  readonly scrollTo: ScrollTo | null; // the span brought into view, when it's here
  // What every mark shows, selected once here rather than by each mark.
  readonly fonts: FontsByObject;
  readonly layout: Layout; // for new text, judged and set as its page's font's
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
    newTextFits: state.drawn.newTextFits.get(index) ?? NO_NEW_TEXT_FITS,
    redactions: state.drawn.redactions.get(index) ?? NO_REDACTIONS,
    matches: state.find.matches.get(index),
    foundAt:
      state.find.current !== null && state.layout.spans.get(state.find.current)?.page === index
        ? state.find.current
        : null,
    shownMismatches: state.shownMismatches,
    // Read only for the rows shown: with none, a first change elsewhere redraws no page.
    changed: state.shownMismatches.size > 0 && changedWordsCount(state) > 0,
    disagreement: state.disagreements.get(index),
    src: pageUrl(state.doc, index, state.scale),
    image: state.images.get(index),
    typingIn: state.draft?.kind === "span" && state.draft.page === index ? state.draft.spanId : null,
    newTextAt: newTextDraftOn(state, index)?.origin ?? null,
    newTextTypedAgain: newTextDraftOn(state, index)?.of?.newTextId ?? null,
    unredacting: here(state.unredacting)?.spanId ?? null,
    focusTo: here(state.focusTo !== null && "spanId" in state.focusTo ? state.focusTo : null),
    focusNewText: focusNewTextOn(state, index),
    howToStart: index === 0 && neverEdited(state),
    scrollTo: here(state.scrollTo),
    fonts: state.layout.fonts,
    layout: state.layout,
    sentences: state.doc.sentences,
    rules: state.doc.fit,
  };
}

/** The new text being typed on page `index`, if it's there. */
function newTextDraftOn(state: EditorState, index: number): NewTextDraft | null {
  return state.draft?.kind === "newText" && state.draft.page === index ? state.draft : null;
}

/** The new text on page `index` focus is sent to, if any. */
function focusNewTextOn(state: EditorState, index: number): number | null {
  const sent = state.focusTo !== null && "newTextId" in state.focusTo ? state.focusTo.newTextId : null;
  const text = sent === null ? undefined : keptNewTexts(state.reading).find((each) => each.newTextId === sent);
  return text?.edit.page === index ? sent : null;
}

/** Whether nothing has been edited yet, undone or not: the how-to-start note shows until the first edit. */
export function neverEdited(state: Pick<EditorState, "history">): boolean {
  return state.history.next === 1;
}

/** The span `spanId` among the page's, if it's there. */
export function spanIn(page: PageState, spanId: string): SpanInfo | undefined {
  return page.spans.find((span) => span.id === spanId);
}

/** What's marked on a page, in page order: a span, or something put on it at a point, as new text is. */
export type PageMark<T> =
  | { readonly span: SpanInfo; readonly placed?: undefined }
  | { readonly placed: T; readonly span?: undefined };

/**
 * The page's spans, in the document's order, with what's put on the page each before the first span below its top:
 * the order Tab reaches them in, as a reader would. Those put at one height go left to right.
 */
export function inPageOrder<T>(
  spans: readonly SpanInfo[],
  placed: readonly { readonly item: T; readonly at: PagePoint }[],
): PageMark<T>[] {
  const waiting = [...placed].sort((a, b) => a.at[1] - b.at[1] || a.at[0] - b.at[0]);
  const marks: PageMark<T>[] = [];
  for (const span of spans) {
    while (waiting[0] !== undefined && waiting[0].at[1] < span.bbox.y0) {
      const next = waiting.shift();
      if (next !== undefined) marks.push({ placed: next.item });
    }
    marks.push({ span });
  }
  return [...marks, ...waiting.map(({ item }) => ({ placed: item }))];
}

/** What's put on page `page` among its spans: each piece of new text at rest by the top of its line, and the field new text is typed in. */
export function placedOnPage(page: Pick<PageState, "edits" | "newTextAt" | "newTextTypedAgain">): PlacedOnPage[] {
  const resting = (page.edits?.newTexts ?? []).filter((text) => text.newTextId !== page.newTextTypedAgain);
  const texts = resting.map((text): PlacedOnPage => {
    const [x, y] = text.edit.origin;
    return { item: text, at: [x, y - text.edit.size * NEW_TEXT_ASCENT_EM] };
  });
  return page.newTextAt === null ? texts : [...texts, { item: "field", at: page.newTextAt }];
}

/** A piece of new text, or the field new text is typed in, where it's put on a page. */
export interface PlacedOnPage {
  readonly item: NewTextReading | "field";
  readonly at: PagePoint;
}
