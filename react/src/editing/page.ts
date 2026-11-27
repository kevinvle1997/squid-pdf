// Selected as parts compared with `shallowEqual`, each the same object until it changes, so a page redraws only then.
import { pageUrl } from "../api/client";
import type { FitRules, Sentences, SpanInfo } from "../api/types";
import { NEW_TEXT_ASCENT_EM } from "./constants";
import {
  changedWordsCount,
  type EditorState,
  type FocusNewText,
  type FocusSpan,
  type Layout,
  type ScrollTo,
} from "./editor";
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

export interface PageState {
  readonly spans: readonly SpanInfo[];
  readonly edits: PageEdits | undefined;
  readonly drawnFrom: PageEdits | undefined;
  readonly strips: readonly Strip[] | undefined;
  readonly fits: PageFits;
  readonly newTextFits: PageNewTextFits;
  readonly redactions: PageRedactions;
  readonly matches: ReadonlyMap<string, readonly Match[]> | undefined;
  readonly foundAt: string | null;
  readonly shownMismatches: ReadonlySet<string>;
  readonly changed: boolean;
  readonly disagreement: PreviewDisagreement | undefined;
  readonly src: string;
  readonly image: FailedImage | undefined;
  readonly typingIn: string | null;
  readonly newTextAt: PagePoint | null;
  readonly newTextTypedAgain: number | null;
  readonly unredacting: string | null;
  readonly focusTo: FocusSpan | null;
  readonly focusNewText: FocusNewText | null;
  readonly howToStart: boolean;
  readonly scrollTo: ScrollTo | null;
  readonly fonts: FontsByObject;
  readonly layout: Layout;
  readonly sentences: Sentences;
  readonly rules: FitRules;
}

export function pageState(state: EditorState, index: number): PageState {
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

function newTextDraftOn(state: EditorState, index: number): NewTextDraft | null {
  return state.draft?.kind === "newText" && state.draft.page === index ? state.draft : null;
}

function focusNewTextOn(state: EditorState, index: number): FocusNewText | null {
  const sent = state.focusTo !== null && "newTextId" in state.focusTo ? state.focusTo : null;
  if (sent === null) return null;
  const text = keptNewTexts(state.reading).find((each) => each.newTextId === sent.newTextId);
  return text?.edit.page === index ? sent : null;
}

export function neverEdited(state: Pick<EditorState, "history">): boolean {
  return state.history.next === 1;
}

export function spanIn(page: PageState, spanId: string): SpanInfo | undefined {
  return page.spans.find((span) => span.id === spanId);
}

export type PageMark<T> =
  | { readonly span: SpanInfo; readonly placed?: undefined }
  | { readonly placed: T; readonly span?: undefined };

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

export function placedOnPage(page: Pick<PageState, "edits" | "newTextAt" | "newTextTypedAgain">): PlacedOnPage[] {
  const resting = (page.edits?.newTexts ?? []).filter((text) => text.newTextId !== page.newTextTypedAgain);
  const texts = resting.map((text): PlacedOnPage => {
    const [x, y] = text.edit.origin;
    return { item: text, at: [x, y - text.edit.size * NEW_TEXT_ASCENT_EM] };
  });
  return page.newTextAt === null ? texts : [...texts, { item: "field", at: page.newTextAt }];
}

export interface PlacedOnPage {
  readonly item: NewTextReading | "field";
  readonly at: PagePoint;
}
