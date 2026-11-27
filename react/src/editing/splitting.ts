// Shift+Enter, or the field's Split: the field ends at the caret, the text past it staying where it is; at a split the
// field already ends at, it joins the two again. A split is only the open field's until an edit it bounds is kept.
import type { SpanInfo } from "../api/types";
import { announcing, type Editor, type EditorState, relaid, unsplitOf } from "./editor";
import { NO_TYPING } from "./fieldHistory";
import type { Line } from "./lines";
import { textIn } from "./project";
import { codePointsIn, partKey, type Region, regionOf, spanIdOf, wireEdit } from "./splits";
import { onlySplit, type SpanDraft, spanDraftOf } from "./typing";
import { sentenceOrNothing } from "./words";

/** Why a split can't be made where the caret is, as the key of the server's sentence for it. */
export type SplitRefusal = "split_changed" | "split_cant_here" | "split_redacted";

/** What the field covers: its line's side of any split, or a span alone, and where that is on its line. */
interface Covered {
  readonly line: Pick<Line, "spans">;
  readonly region: Region;
  readonly runs: readonly string[];
}

/** A field to open in place of the one split or joined. */
interface Reopened {
  readonly region: Region;
  readonly runs: readonly string[];
  readonly caret: number;
}

/**
 * Split at `caret`, in the field's units, or join where it stands at a split: the field opens over what it covers now,
 * and where its caret goes is returned. Where it can't, none is: the field says why, its words as they were.
 */
export function splitOrJoin(editor: Editor, caret: number): number | null {
  const state = editor.store.get();
  const draft = spanDraftOf(state);
  const covered = draft === null ? null : coveredBy(state, draft);
  if (draft === null || covered === null) return null;
  const made = joinedAt(state, covered, caret) ?? splitAt(state, covered, caret);
  if (typeof made === "string") {
    const sentence = sentenceOrNothing(state.doc.sentences[made]);
    editor.store.set({ draft: { ...draft, splitRefused: made }, announcement: announcing(sentence) });
    return null;
  }
  const said = made.joined ? "split_joined" : "split_made";
  reopen(editor, draft, { field: made.field, said });
  return made.field.caret;
}

/**
 * Focus gone to the mark of the text past a split nothing's typed against: its field opens, the split still between
 * them, which closing the first would forget, and the mark with it. Whether it did.
 */
export function openAcross(editor: Editor, markId: string): boolean {
  const state = editor.store.get();
  const draft = spanDraftOf(state);
  if (draft?.split === undefined || !onlySplit(draft)) return false;
  const line = state.layout.lines.get(markId);
  const side = line === undefined ? null : coveredBy(state, { ...draft, spanId: markId, runs: null, text: "" });
  if (line === undefined || side === null || side.region.lineId !== draft.split.lineId) return false;
  const runs = line.spans.map((span) => textIn(state.reading, span));
  reopen(editor, draft, { field: { region: side.region, runs, caret: 0 }, said: null });
  return true;
}

/** Why the open field's last split couldn't be made, in words; none once typed in again. */
export function splitRefusalOf(state: EditorState): string | null {
  const refused = spanDraftOf(state)?.splitRefused;
  return refused === undefined ? null : sentenceOrNothing(state.doc.sentences[refused]);
}

/** What the draft's field covers; none for new text, or a span the layout lacks. */
function coveredBy(state: EditorState, draft: SpanDraft): Covered | null {
  const { layout } = state;
  const span = layout.spans.get(draft.spanId);
  if (span === undefined) return null;
  const line = layout.lines.get(span.id) ?? { spans: [span] };
  const base = unsplitOf(layout);
  const runs = line.spans.map((each) => ({ span_id: each.id, text: each.text }));
  const region = regionOf(base, wireEdit(base, layout.parts, { kind: "replace_runs", runs }));
  return region === null ? null : { line, region, runs: draft.runs ?? [draft.text] };
}

/** How many code points the line a region is on has. */
function lineLength(state: EditorState, region: Region): number {
  const base = unsplitOf(state.layout);
  const spans = base.lines.get(region.lineId)?.spans ?? [base.spans.get(region.lineId)].filter((s) => s !== undefined);
  return spans.reduce((total, span) => total + codePointsIn(span.text), 0);
}

/** The field joined to the side beyond the split the caret stands at; null with the caret at no split. */
function joinedAt(state: EditorState, covered: Covered, caret: number): { joined: true; field: Reopened } | null {
  const { region, runs } = covered;
  const typed = runs.join("");
  if (caret >= typed.length && region.to < lineLength(state, region)) {
    const next = sideAt(state, region, region.to);
    if (next === null) return null;
    const joined = joinedRuns(state, { left: runs, right: next.runs, region, at: region.to });
    return { joined: true, field: { region: { ...region, to: next.region.to }, runs: joined, caret: typed.length } };
  }
  if (caret <= 0 && region.from > 0) {
    const before = sideAt(state, region, region.from - 1);
    if (before === null) return null;
    const joined = joinedRuns(state, { left: before.runs, right: runs, region, at: region.from });
    const words = before.runs.join("").length;
    return { joined: true, field: { region: { ...region, from: before.region.from }, runs: joined, caret: words } };
  }
  return null;
}

/** The side of `region`'s line holding code point `at`, and what it reads. */
function sideAt(state: EditorState, region: Region, at: number): Covered | null {
  const sideSpan = partHolding(state, region.lineId, at);
  if (sideSpan === undefined) return null;
  const line = state.layout.lines.get(sideSpan.id) ?? { spans: [sideSpan] };
  const base = unsplitOf(state.layout);
  const runs = line.spans.map((each) => ({ span_id: each.id, text: each.text }));
  const found = regionOf(base, wireEdit(base, state.layout.parts, { kind: "replace_runs", runs }));
  return found === null ? null : { line, region: found, runs: line.spans.map((each) => textIn(state.reading, each)) };
}

/** The spans of the line `lineId` names, as the document has them. */
function lineSpans(state: EditorState, lineId: string): readonly SpanInfo[] {
  const base = unsplitOf(state.layout);
  const span = base.spans.get(lineId);
  return base.lines.get(lineId)?.spans ?? (span === undefined ? [] : [span]);
}

/** The span of the line code point `at` falls in, and how far into it. */
function placeIn(state: EditorState, lineId: string, at: number): { span: SpanInfo; offset: number } | null {
  let before = 0;
  for (const span of lineSpans(state, lineId)) {
    const length = codePointsIn(span.text);
    if (at < before + length) return { span, offset: at - before };
    before += length;
  }
  return null;
}

/** The id a part starting at code point `at` of the line has. */
function keyAt(state: EditorState, lineId: string, at: number): string | null {
  const place = placeIn(state, lineId, at);
  return place === null ? null : partKey(place.span.id, place.offset);
}

/** The part shown now that holds code point `at` of the line. */
function partHolding(state: EditorState, lineId: string, at: number): SpanInfo | undefined {
  const place = placeIn(state, lineId, at);
  if (place === null) return undefined;
  return state.layout.ordered.find((each) => {
    const part = state.layout.parts.get(each.id) ?? { spanId: each.id, start: 0, end: codePointsIn(each.text) };
    return part.spanId === place.span.id && part.start <= place.offset && place.offset < part.end;
  });
}

/** Two sides' runs as one's: where the split fell inside a span, its two halves are one run again. */
function joinedRuns(
  state: EditorState,
  { left, right, region, at }: { left: readonly string[]; right: readonly string[]; region: Region; at: number },
): string[] {
  const insideSpan = (placeIn(state, region.lineId, at)?.offset ?? 0) > 0;
  if (!insideSpan) return [...left, ...right];
  return [...left.slice(0, -1), `${left.at(-1) ?? ""}${right[0] ?? ""}`, ...right.slice(1)];
}

/**
 * The field split at `caret`, over the side holding what's changed, else the side before the caret; why it can't
 * be, where the words there aren't the page's, or there's nothing either side.
 */
function splitAt(
  state: EditorState,
  covered: Covered,
  caret: number,
): { joined: false; field: Reopened } | SplitRefusal {
  const { line, region, runs } = covered;
  if (line.spans.some((span) => span.form_field)) return "split_cant_here";
  if (line.spans.some((span) => state.reading.spans.get(spanIdOf(span.id))?.redacted != null)) return "split_redacted";
  const typed = runs.join("");
  const original = line.spans.map((span) => span.text).join("");
  const place = originalPlaceOf(original, typed, caret);
  if (place === null) return "split_changed";
  const { at, side } = place;
  if (at <= 0 || at >= original.length) return "split_cant_here";
  const cut = region.from + codePointsIn(original.slice(0, at));
  const kept = side === "left" ? { ...region, to: cut } : { ...region, from: cut };
  const keptRange = side === "left" ? ([0, caret] as const) : ([caret, typed.length] as const);
  const field = { region: kept, runs: clipped(runs, keptRange), caret: side === "left" ? caret : 0 };
  return { joined: false, field };
}

/**
 * Where in the page's words `caret`, in the words typed, stands, and which side keeps the field: the side the change
 * is on, else the one before the caret. None where the words on neither side of the caret are the page's.
 */
export function originalPlaceOf(
  original: string,
  typed: string,
  caret: number,
): { at: number; side: "left" | "right" } | null {
  const sameStart = sharedStart(original, typed);
  const sameEnd = sharedStart(reversed(original), reversed(typed));
  if (caret <= sameStart) return { at: caret, side: typed === original ? "left" : "right" };
  const fromEnd = typed.length - caret;
  return fromEnd <= sameEnd ? { at: original.length - fromEnd, side: "left" } : null;
}

/** How many UTF-16 units two texts start alike. */
function sharedStart(a: string, b: string): number {
  let at = 0;
  while (at < a.length && a[at] === b[at]) at++;
  return at;
}

function reversed(text: string): string {
  return text.split("").reverse().join("");
}

/** The runs' words from `from` to `to` of them all, read as one: each run with some of them, an empty one kept inside. */
function clipped(runs: readonly string[], [from, to]: readonly [number, number]): string[] {
  let start = 0;
  return runs.flatMap((run) => {
    const end = start + run.length;
    const first = Math.max(from - start, 0);
    const last = Math.min(to - start, run.length);
    const inside = start < to && end > from;
    start = end;
    return inside ? [run.slice(first, last)] : [];
  });
}

/** Whether the field has no words the history lacks, as it's split or joined. */
function nothingNewIn(state: EditorState, draft: SpanDraft): boolean {
  const words = draft.runs ?? [draft.text];
  // Split or joined already, the reading is the page's under the field: what it was left with says what's new.
  const left = draft.asSplit;
  if (draft.split !== undefined) return left?.length === words.length && left.every((run, i) => run === words[i]);
  const span = state.layout.spans.get(draft.spanId);
  const spans = state.layout.lines.get(draft.spanId)?.spans ?? (span === undefined ? [] : [span]);
  return spans.length === words.length && spans.every((each, i) => textIn(state.reading, each) === words[i]);
}

/** Open the field `made` covers in place of the one split or joined, its split the open field's until it's kept. */
function reopen(
  editor: Editor,
  draft: SpanDraft,
  { field, said }: { field: Reopened; said: "split_made" | "split_joined" | null },
): void {
  const state = editor.store.get();
  const base = unsplitOf(state.layout);
  const untouched = nothingNewIn(state, draft);
  const { layout, reading } = relaid(state, state.doc, { base, taken: field.region });
  const withDraft = { ...state, layout, reading };
  const spanId = keyAt(withDraft, field.region.lineId, field.region.from);
  const first = spanId === null ? undefined : layout.spans.get(spanId);
  if (spanId === null || first === undefined) return;
  const side = layout.lines.get(spanId);
  const spans = side?.spans ?? [first];
  // As many runs as the side has spans, any words past them on its last.
  const runs = spans.map((_, index) =>
    index === spans.length - 1 ? field.runs.slice(index).join("") : (field.runs[index] ?? ""),
  );
  const next: SpanDraft = {
    ...draft,
    spanId,
    runs: side === undefined ? null : runs,
    text: runs.join(""),
    split: field.region,
    caret: field.caret,
    press: null,
    strategy: undefined,
    value: null,
    valueEditorOpen: false,
    fieldHistory: NO_TYPING,
    splitRefused: undefined,
    asSplit: untouched ? runs : undefined,
  };
  const heard = said === null ? {} : { announcement: announcing(sentenceOrNothing(state.doc.sentences[said])) };
  editor.store.set({ layout, reading, draft: next, focusTo: null, ...heard });
}
