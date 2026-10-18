// Typing in place: a draft, held apart from the history until it ends. It ends once, however
// it ends: Enter, Escape, leaving the field, or an export taking it along.
import type { FitRules, FontInfo, SpanInfo, Strategy } from "../api/types";
import { change, type Editor, type EditorState, say, spoken } from "./editor";
import { substituteOf } from "./faces";
import { type Fit, fitOf, runsPast, troublesOf, wayDrawn, waysOffered } from "./fit";
import type { Reading } from "./project";

/** The text being typed into a span, not yet in the history. */
export interface Draft {
  readonly spanId: string;
  readonly page: number;
  readonly atPt: number | null; // where in the span the press was, from its start: the word to select
  readonly text: string;
  readonly strategy: Strategy | undefined; // the way out of a long line chosen, kept while it's offered
}

/** Start typing into a span, from what it reads now. A redacted span asks first, in place. */
export function edit(editor: Editor, spanId: string, atPt: number | null): void {
  const { store } = editor;
  const { layout, reading, doc } = store.get();
  const span = layout.spans.get(spanId);
  if (span === undefined) return;
  // Un-redacting by accident is a leak: the one edit that asks first (quality.md, Modals and prompts).
  if (reading.spans.get(spanId)?.redacted) {
    store.set({ unredacting: { spanId, atPt }, said: spoken(doc.copy.undo_redaction) });
    return;
  }
  store.set({ draft: draftOf(span, { reading, atPt }), focusTo: null, unredacting: null });
}

/** Typing into `span` as it reads in `reading`, the press `atPt` points from its start. */
export function draftOf(span: SpanInfo, { reading, atPt }: { reading: Reading; atPt: number | null }): Draft {
  const now = reading.spans.get(span.id);
  return { spanId: span.id, page: span.page, atPt, text: now?.text ?? span.text, strategy: now?.strategy };
}

/** The draft, the span typed into and its font: none while nothing's typed in, or its font is unknown. */
export function draftSpanOf(state: EditorState): { draft: Draft; span: SpanInfo; font: FontInfo } | undefined {
  const { draft, layout } = state;
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  const font = span === undefined ? undefined : layout.fonts.get(span.font);
  if (draft === null || span === undefined || font === undefined) return undefined;
  return { draft, span, font };
}

/** How the draft's span fits what's typed: nothing while nothing's typed in, or its font is unknown. */
function draftFitOf(state: EditorState): Fit | undefined {
  const typing = draftSpanOf(state);
  if (typing === undefined) return undefined;
  return fitOf(typing.span, { font: typing.font, text: typing.draft.text, rules: state.doc.fit });
}

/** The way out the draft's line is drawn with: the one chosen while it's offered, else as typed. */
function wayIn(state: EditorState): Strategy {
  const fit = draftFitOf(state);
  return fit === undefined ? "as-is" : wayDrawn(state.draft?.strategy, fit);
}

/** The user chose how a long line fits, in the chip or with the arrow keys: drawn in the field at once, and said. */
export function chooseWayOut(editor: Editor, way: Strategy): void {
  const { draft, doc } = editor.store.get();
  if (draft === null) return;
  editor.store.set({ draft: { ...draft, strategy: way }, said: spoken(doc.copy.options[way]?.label ?? way) });
}

/** Up or Down in the field: the way out before or after the one drawn, round the list; false with none to choose. */
export function stepWayOut(editor: Editor, by: 1 | -1): boolean {
  const state = editor.store.get();
  const fit = draftFitOf(state);
  const offered = fit === undefined ? [] : waysOffered(fit);
  if (fit === undefined || offered.length === 0) return false;
  const at = offered.indexOf(wayDrawn(state.draft?.strategy, fit));
  const next = offered[(at + by + offered.length) % offered.length];
  if (next !== undefined) chooseWayOut(editor, next);
  return true;
}

/** A fit's troubles less their numbers: said again when this changes, not as the numbers tick by with each letter. */
function troubleKindOf(fit: Fit, rules: FitRules): string {
  return `${fit.missing.join("")} ${runsPast(fit.deltaPt, fit.roomPt, rules)}`;
}

/** What the field says of the ways out as they're first offered: the keys that choose one are the field's own. */
const WAYS_KEYS = "Up or Down chooses how it fits";

/**
 * What the draft's span would get wrong reading `text`, in the server's words, nothing when it fits;
 * and whether there's a way out of a long line to choose, past leaving it long.
 */
export function troublesIn(state: EditorState, text: string): { said: string[]; kind: string; offers: boolean } {
  const typing = draftSpanOf(state);
  if (typing === undefined) return { said: [], kind: "", offers: false };
  const { draft, span, font } = typing;
  const { fit: rules, copy } = state.doc;
  // A form field draws it, not the page: whatever is typed, the edit is left out.
  if (span.form_field) return { said: [copy.form_field_not_edited], kind: "form field", offers: false };
  // Taking text out isn't a change: said while the field is empty.
  if (isEmpty(text)) return { said: [copy.empty], kind: "empty", offers: false };
  const fit = fitOf(span, { font, text, rules });
  const substitute = substituteOf(span, font);
  const way = wayDrawn(draft.strategy, fit);
  // That an edit here won't match the text around it is said once, as typing starts (`editNote`), not with each letter.
  return {
    said: troublesOf(fit, { rules, copy, substitute, way }),
    kind: `${troubleKindOf(fit, rules)} ${way}`,
    offers: waysOffered(fit).length > 0,
  };
}

// A tab or any line break shows as a gap, so a space keeps the words apart.
const SPACING_CONTROL = /[\t\n\v\f\r\u0085\u2028\u2029]/g;

// The rest of what check_text (editing/edits.py) refuses draws nothing, so it's dropped.
const NOT_ON_A_LINE = /[\p{Cc}\p{Zl}\p{Zp}\p{Cs}\u202A-\u202E\u2066-\u2069]/gu;

export function typeInto(editor: Editor, typed: string): void {
  const { store } = editor;
  const state = store.get();
  if (state.draft === null) return;
  const text = typed.replace(SPACING_CONTROL, " ").replace(NOT_ON_A_LINE, "");
  const was = troublesIn(state, state.draft.text);
  const now = troublesIn(state, text);
  // A trouble is said as it appears or changes, not as its numbers tick by with each letter.
  const offered = now.offers && !was.offers ? [WAYS_KEYS] : [];
  const said = now.kind !== was.kind && now.said.length > 0 ? spoken([...now.said, ...offered].join("; ")) : state.said;
  store.set({ draft: { ...state.draft, text }, said });
}

/** Enter: what was typed goes in and focus goes back to the span, unless the field is empty, which stays open. */
export function enter(editor: Editor): void {
  const { draft, doc } = editor.store.get();
  if (draft !== null && isEmpty(draft.text)) {
    say(editor, doc.copy.empty);
    return;
  }
  finish(editor, true, { returnFocus: true });
}

/** Whether typed text says nothing: no change, since taking text out is redaction's job. */
function isEmpty(text: string): boolean {
  return text.trim() === "";
}

/**
 * End the typing: `keep` puts what was typed in the history. `returnFocus` sends focus back to
 * the span, as Enter and Escape do; leaving the field any other way has put it somewhere already.
 */
export function finish(editor: Editor, keep: boolean, { returnFocus = false } = {}): void {
  const { store } = editor;
  const { draft, layout, reading } = store.get();
  if (draft === null) return;
  const ended = { draft: null, ...(returnFocus && { focusTo: { spanId: draft.spanId } }) };
  const now = reading.spans.get(draft.spanId);
  const was = now?.text ?? layout.spans.get(draft.spanId)?.text;
  // Only a way out on offer goes with the edit: the server draws any other as typed, and says so.
  const way = wayIn(store.get());
  const strategy = way === "as-is" ? undefined : way;
  const unchanged = draft.text === was && strategy === now?.strategy;
  // Emptying a span isn't a replacement: taking text out is redaction's job.
  if (!keep || unchanged || isEmpty(draft.text)) {
    store.set(ended);
    return;
  }
  const replace = { kind: "replace" as const, span_id: draft.spanId, text: draft.text, ...(strategy && { strategy }) };
  change(editor, { kind: "add", edits: [replace] }, { ...ended, said: spoken(`Changed to ${draft.text}`) });
}
