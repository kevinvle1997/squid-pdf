// Typing in place: a draft, held apart from the history until it ends. It ends once, however
// it ends: Enter, Escape, leaving the field, or an export taking it along.
import type { FitRules, FontInfo, Insert, Replace, SpanInfo, Strategy } from "../api/types";
import { type Press, selectionAt } from "./caret";
import { readDocumentDates } from "./documentDates";
import { type Also, announce, announcing, change, type Editor, type EditorState } from "./editor";
import { type Fit, fitOf, letterTroublesOf, runsPast, troublesOf, wayDrawn, waysOffered } from "./fit";
import { editableValueIn, emailCountIn, type HeldValue, heldOf, heldThrough, heldValueIn } from "./formattedValues";
import type { HistoryAction } from "./history";
import { editNote, type Note } from "./marks";
import {
  fontOfNewText,
  inShippedFace,
  insertOf,
  keptOnPage,
  type NewTextDraft,
  newTextDrawingOf,
  newTextSpanOf,
  newTextVerdictOf,
  sameInsert,
} from "./newText";
import { type Reading, textIn } from "./project";
import { landedNewTextFitOf } from "./render";
import { fontOf, shownNameIn } from "./spanFonts";
import { drawingOf } from "./substitutes";
import { editVerdictOf, shortWarningOf, type Verdict } from "./verdicts";
import { fill, sentenceOrNothing } from "./words";

/** The text being typed into a span, not yet in the history. */
export interface SpanDraft {
  readonly kind: "span";
  readonly spanId: string;
  readonly page: number;
  readonly press: Press | null; // the press that opened it, if one did: where the caret goes
  readonly text: string;
  readonly strategy: Strategy | undefined; // the way out of a long line chosen, kept while it's offered
  readonly valueEditorOpen: boolean; // a date's or an amount's editor open in the slip (valueEditor.ts)
  readonly value: HeldValue | null; // the date or amount its editor is offered for, held once it's opened or typed in
}

/** What's being typed: into a span, or new text. */
export type Draft = SpanDraft | NewTextDraft;

/** The draft, while it's a span's. */
export function spanDraftOf(state: Pick<EditorState, "draft">): SpanDraft | null {
  return state.draft?.kind === "span" ? state.draft : null;
}

/** Start typing into a span, from what it reads now. A redacted span asks first, in place. */
export function edit(editor: Editor, spanId: string, press: Press | null): void {
  const { store } = editor;
  const { layout, reading, doc } = store.get();
  const span = layout.spans.get(spanId);
  if (span === undefined) return;
  // Un-redacting by accident is a leak: the one edit that asks first (quality.md, Modals and prompts).
  if (reading.spans.get(spanId)?.redacted) {
    store.set({ unredacting: { spanId, press }, announcement: announcing(doc.sentences.undo_redaction) });
    return;
  }
  readDocumentDates(editor);
  store.set({ draft: draftOf(span, { reading, press }), focusTo: null, unredacting: null });
}

/**
 * Typing into `span` as it reads in `reading`, opened by `press`. No value is held yet: until the user types or opens
 * its editor, the value offered follows the dates as they're read, so the one pressed on can still be.
 */
export function draftOf(span: SpanInfo, { reading, press }: { reading: Reading; press: Press | null }): SpanDraft {
  const now = reading.spans.get(span.id);
  const text = now?.text ?? span.text;
  return {
    kind: "span",
    spanId: span.id,
    page: span.page,
    press,
    text,
    strategy: now?.strategy,
    valueEditorOpen: false,
    value: null,
  };
}

/**
 * What the draft holds of the date or amount offered once it reads `text`, typed in: the one held, carried along
 * as the words before it change (`heldThrough`), else the one the press that opened the field landed on, else the
 * first (`editableValueIn`), and none while there's none. `lost`, when the one held is gone from the text: its kind
 * and where it lay are still held, so a value of that kind back over it is the one held again, as a year retyped
 * by hand passes through no date at all. Meanwhile, and if none ever comes back, the control offers what's there.
 */
function heldIn(state: EditorState & { draft: SpanDraft }, text: string): { value: HeldValue | null; lost: boolean } {
  const { draft, layout, reading, dates } = state;
  const span = layout.spans.get(draft.spanId);
  if (span === undefined) return { value: null, lost: false };
  const held = draft.value === null ? null : heldThrough(draft.value, { before: draft.text, after: text });
  const glyphs = fontOf(layout.fonts, span)?.glyphs ?? {};
  const at = draft.press === null ? undefined : selectionAt(textIn(reading, span), draft.press, { glyphs, span })[0];
  const value = editableValueIn(text, { dates, at, held });
  const lost = held !== null && heldValueIn(text, { dates, held }) === undefined;
  if (lost) return { value: held, lost };
  return { value: value === undefined ? null : heldOf(value), lost };
}

/** The draft, the span typed into and its font: none while nothing's typed in, or its font is unknown. */
export function draftSpanOf(state: EditorState): { draft: SpanDraft; span: SpanInfo; font: FontInfo } | undefined {
  const { layout } = state;
  const draft = spanDraftOf(state);
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  const font = span === undefined ? undefined : fontOf(layout.fonts, span);
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
  return fit === undefined ? "as-is" : wayDrawn(spanDraftOf(state)?.strategy, fit);
}

/** The user chose how a long line fits, in the chip or with the arrow keys: drawn in the field at once, and announced. */
export function chooseWayOut(editor: Editor, way: Strategy): void {
  const { doc } = editor.store.get();
  const draft = spanDraftOf(editor.store.get());
  if (draft === null) return;
  editor.store.set({
    draft: { ...draft, strategy: way },
    announcement: announcing(doc.sentences.options[way]?.label ?? way),
  });
}

/** Up or Down in the field: the way out before or after the one drawn, round the list; false with none to choose. */
export function stepWayOut(editor: Editor, by: 1 | -1): boolean {
  const state = editor.store.get();
  const fit = draftFitOf(state);
  const offered = fit === undefined ? [] : waysOffered(fit);
  if (fit === undefined || offered.length === 0) return false;
  const at = offered.indexOf(wayDrawn(spanDraftOf(state)?.strategy, fit));
  const next = offered[(at + by + offered.length) % offered.length];
  if (next !== undefined) chooseWayOut(editor, next);
  return true;
}

/** A fit's troubles less their numbers: announced again when this changes, not as the numbers tick by with each letter. */
function troubleKindOf(fit: Fit, rules: FitRules): string {
  return `${fit.missing.join("")} ${runsPast(fit.deltaPt, fit.roomPt, rules)}`;
}

/** What the field says of the ways out as they're first offered: the keys that choose one are the field's own. */
const WAYS_KEYS = "Up or Down chooses how it fits";

/** What reading `text` comes to in the draft's span: what won't come out as typed, and the edit's verdict. */
interface Troubles {
  readonly sentences: string[]; // in the server's words, a trouble each; none when it fits
  readonly kind: string; // the troubles and the verdict's kind, less their numbers: heard again when this changes
  readonly offers: boolean; // there's a way out of a long line to choose, past leaving it long
  readonly verdict: Verdict | null; // none while nothing's typed in, or its font is unknown
}

/** What the draft's span would get wrong reading `text`, the ways out it offers, and what it comes to. */
export function troublesIn(state: EditorState, text: string): Troubles {
  if (state.draft?.kind === "newText") return newTextTroublesIn(state, state.draft, text);
  const typing = draftSpanOf(state);
  if (typing === undefined) return { sentences: [], kind: "", offers: false, verdict: null };
  const { draft, span, font } = typing;
  const { fit: rules, sentences } = state.doc;
  // Neither is an edit: what the span's own words come to holds.
  const unedited = { offers: false, verdict: span };
  // A form field draws it, not the page: whatever is typed, the edit is left out.
  if (span.form_field) return { sentences: [sentences.form_field_not_edited], kind: "form field", ...unedited };
  // Taking text out isn't a change: told while the field is empty.
  if (isEmpty(text)) return { sentences: [sentences.empty], kind: "empty", ...unedited };
  const verdict = editVerdictOf(state.drawn, { span, font, text, strategy: draft.strategy });
  const fit = fitOf(span, { font, text, rules });
  const drawing = drawingOf(span, { font, text, missing: fit.missing });
  const way = wayDrawn(draft.strategy, fit);
  const email = emailBroken(textIn(state.reading, span), text) ? [sentenceOrNothing(sentences.email_broken)] : [];
  // That an edit here won't match is announced in full once, as typing starts (`editNote`), not with each letter.
  return {
    sentences: [...troublesOf(fit, { rules, sentences, drawing, way }), ...email],
    kind: `${troubleKindOf(fit, rules)} ${way} ${shortWarningOf(verdict) ?? ""} ${email.length}`,
    offers: waysOffered(fit).length > 0,
    verdict,
  };
}

/** What new text being typed would get wrong reading `text`, and what it comes to: only letters, as it has no width to fit. */
function newTextTroublesIn(state: EditorState, draft: NewTextDraft, text: string): Troubles {
  const insert = insertOf({ ...draft, text });
  // Typed into again and not changed: its render's verdict holds.
  const fit =
    draft.of !== null && sameInsert(draft.of.edit, insert) ? landedNewTextFitOf(state.drawn, draft.of) : undefined;
  const verdict = newTextVerdictOf(state.layout, { insert, fit });
  const drawing = newTextDrawingOf(state.layout, insert);
  const kind = `${drawing.switched.join("")} ${drawing.leftOut.join("")} ${shortWarningOf(verdict) ?? ""}`;
  return { sentences: letterTroublesOf(drawing, state.doc.sentences), kind, offers: false, verdict };
}

/** Whether `typed` reads as other than the emails `before` held: one broken, or run into another. */
function emailBroken(before: string, typed: string): boolean {
  const emails = emailCountIn(before);
  return emails > 0 && emailCountIn(typed) !== emails;
}

// A tab or any line break shows as a gap, so a space keeps the words apart.
const SPACING_CONTROL = /[\t\n\v\f\r\u0085\u2028\u2029]/g;

// The rest of what check_text (editing/edits.py) refuses draws nothing, so it's dropped.
const NOT_ON_A_LINE = /[\p{Cc}\p{Zl}\p{Zp}\p{Cs}\u202A-\u202E\u2066-\u2069]/gu;

/** Type `typed` into the field: what it warns of is heard as it changes. */
export function typeInto(editor: Editor, typed: string): void {
  const state = editor.store.get();
  const { draft } = state;
  if (draft === null) return;
  if (draft.kind === "span") {
    editor.store.set(typedState({ ...state, draft }, typed));
    return;
  }
  const text = lineOf(typed);
  editor.store.set({ draft: { ...draft, text }, announcement: heardAsTyped({ ...state, draft }, text) });
}

/** What typing `typed` into a span's draft makes of the state: its text, and a trouble announced as it appears. */
export function typedState(
  state: EditorState & { draft: SpanDraft },
  typed: string,
): Pick<EditorState, "announcement"> & Partial<Pick<EditorState, "focusTo">> & { draft: SpanDraft } {
  const text = lineOf(typed);
  const announcement = heardAsTyped(state, text);
  // The value whose editor is open gone from the text, the editor goes with it, and focus goes to the field: where it
  // is when typed there, and never to the page's body from the editor going under it. Never another value's editor in
  // its place, taking focus from the field typed in.
  const { value, lost } = heldIn(state, text);
  const closing = state.draft.valueEditorOpen && lost;
  const valueEditorOpen = state.draft.valueEditorOpen && !lost;
  return {
    draft: { ...state.draft, text, value, valueEditorOpen },
    announcement,
    ...(closing && { focusTo: { spanId: state.draft.spanId } }),
  };
}

/** Type `typed` into new text: grown past the room to its right, its start moves left, so its end stays on its page. */
export function typeIntoNewText(
  editor: Editor,
  typed: string,
  { widthOf }: { widthOf: (insert: Insert) => number },
): void {
  const state = editor.store.get();
  const { draft, doc } = state;
  const info = draft?.kind === "newText" ? doc.pages[draft.page] : undefined;
  if (draft?.kind !== "newText" || info === undefined) return;
  const text = lineOf(typed);
  const widthPt = widthOf(insertOf({ ...draft, text }));
  const origin = keptOnPage(info, { origin: draft.origin, size: draft.style.size }, widthPt);
  editor.store.set({ draft: { ...draft, text, origin }, announcement: heardAsTyped({ ...state, draft }, text) });
}

/** What's typed as one line: a gap for each break, and nothing a line can't draw. */
function lineOf(typed: string): string {
  return typed.replace(SPACING_CONTROL, " ").replace(NOT_ON_A_LINE, "");
}

/** What typing `text` makes heard: a trouble as it appears or changes, the chip's short warning as it changes kind. */
function heardAsTyped(state: EditorState & { draft: Draft }, text: string): EditorState["announcement"] {
  const was = troublesIn(state, state.draft.text);
  const now = troublesIn(state, text);
  // Never per letter: only what's new is heard.
  const offered = now.offers && !was.offers ? [WAYS_KEYS] : [];
  const warning = now.verdict === null ? null : shortWarningOf(now.verdict);
  const warnedBefore = was.verdict === null ? null : shortWarningOf(was.verdict);
  const warningHeard = warning === warnedBefore || now.verdict === null ? [] : [heardOf(state, now.verdict)];
  const changedKind = now.kind !== was.kind && (now.sentences.length > 0 || warningHeard.length > 0);
  // A sentence a reading persisted by an earlier build lacks is nothing, and leaves no joiner behind.
  const parts = [...now.sentences, ...warningHeard, ...offered].filter((part) => part !== "");
  // Such a reading lacks the joiner too: a space, since join's own default is a comma.
  const heard = parts.join(state.doc.sentences.join_parts ?? " ");
  return changedKind ? announcing(heard) : state.announcement;
}

/** The draft's full warning on `verdict`, shown in the chip: none when its words keep their look, or it's muted. */
export function draftNoteOf(state: EditorState, verdict: Verdict): Note | null {
  const { draft, layout, doc, mutedFonts } = state;
  if (draft?.kind === "newText") {
    const insert = insertOf(draft);
    // A face we ship draws it as asked: there's nothing to warn of but a letter it lacks.
    if (inShippedFace(insert)) return null;
    const span = newTextSpanOf(layout, insert);
    const muted = mutedFonts.has(shownNameIn(layout.fonts, span));
    return editNote(span, { verdict, font: fontOfNewText(layout, insert), sentences: doc.sentences, muted });
  }
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  if (span === undefined) return null;
  const muted = mutedFonts.has(shownNameIn(layout.fonts, span));
  return editNote(span, { verdict, font: fontOf(layout.fonts, span), sentences: doc.sentences, muted });
}

// What's heard of each verdict where no full warning is: the chip's short words, as the server says them whole.
const VERDICT_SENTENCES: Record<Verdict["fidelity"], "typed_identical" | "typed_different" | "typed_similar"> = {
  exact: "typed_identical",
  approximate: "typed_different",
  substitute: "typed_similar",
};

/** What's heard as the words typed come to `verdict`: its full warning as the chip shows it, else its short words. */
function heardOf(state: EditorState, verdict: Verdict): string {
  const note = draftNoteOf(state, verdict);
  if (note !== null) return [note.sentence, note.why].filter(Boolean).join(" ");
  return sentenceOrNothing(state.doc.sentences[VERDICT_SENTENCES[verdict.fidelity]]);
}

/** What a signed document's first edit says in full as it starts, in the server's words: nothing once an edit is in. */
export function signatureWarningOf({ doc, reading }: Pick<EditorState, "doc" | "reading">): string | null {
  // The signature is broken from the first edit on, and the line under the bar says so.
  if (!doc.signed || reading.edits.length > 0) return null;
  return doc.sentences.edit_breaks_signature;
}

/** The field took focus as typing starts: what an edit here warns of, and what's wrong already, is heard once. */
export function fieldFocused(editor: Editor): void {
  const state = editor.store.get();
  const { draft, layout } = state;
  const span = draft?.kind === "span" ? layout.spans.get(draft.spanId) : undefined;
  if (draft === null || (draft.kind === "span" && span === undefined)) return;
  const { sentences, verdict } = troublesIn(state, draft.text);
  const comesTo = verdict ?? span;
  const note = comesTo === undefined ? null : draftNoteOf(state, comesTo);
  const trouble = sentences.join("\n");
  const heard = [signatureWarningOf(state), note?.sentence, note?.why, trouble].filter(Boolean).join(" ");
  if (heard !== "") announce(editor, heard);
}

/** Enter: what was typed goes in and focus goes back to the span, unless the field is empty, which stays open. */
export function enter(editor: Editor): void {
  const { draft, doc } = editor.store.get();
  // New text with nothing typed is put away, as Escape does; typed into again and emptied, it's taken out.
  if (draft?.kind === "newText") {
    finish(editor, true, { returnFocus: true });
    return;
  }
  if (draft !== null && isEmpty(draft.text)) {
    announce(editor, doc.sentences.empty);
    return;
  }
  finish(editor, true, { returnFocus: true });
}

/** Whether typed text says nothing: no change, since taking text out is redaction's job. */
function isEmpty(text: string): boolean {
  return text.trim() === "";
}

/** The edit a span's typing is kept as once it ends: none while no span is typed in, or nothing would change. */
export function keptEditOf(state: EditorState): Replace | null {
  const { layout, reading } = state;
  const draft = spanDraftOf(state);
  if (draft === null) return null;
  const now = reading.spans.get(draft.spanId);
  const was = now?.text ?? layout.spans.get(draft.spanId)?.text;
  // Only a way out on offer goes with the edit: the server draws any other as typed, and says so.
  const way = wayIn(state);
  const strategy = way === "as-is" ? undefined : way;
  const unchanged = draft.text === was && strategy === now?.strategy;
  // Emptying a span isn't a replacement: taking text out is redaction's job.
  if (unchanged || isEmpty(draft.text)) return null;
  return { kind: "replace", span_id: draft.spanId, text: draft.text, ...(strategy && { strategy }) };
}

/**
 * What ending the typing makes, for an action to make in its own update: the step it adds, with no
 * edit unless `keep` and the words changed, and the rest it sets. `returnFocus` is `finish`'s.
 */
export function typingEnd(
  state: EditorState,
  keep: boolean,
  { returnFocus = false } = {},
): { typed: HistoryAction; ended: Partial<EditorState> } {
  const { draft, doc } = state;
  const nothing: HistoryAction = { kind: "add", edits: [] };
  if (draft === null) return { typed: nothing, ended: {} };
  if (draft.kind === "newText") return newTextTypingEnd(state, draft, { keep, returnFocus });
  const ended = { draft: null, ...(returnFocus && { focusTo: { spanId: draft.spanId } }) };
  const replace = keptEditOf(state);
  if (!keep || replace === null) return { typed: nothing, ended };
  return {
    typed: { kind: "add", edits: [replace] },
    ended: { ...ended, announcement: announcing(fill(doc.sentences.changed_to, { text: draft.text })) },
  };
}

/** What ending new text's typing makes: the text, its next version, nothing, or, emptied, its taking out. */
function newTextTypingEnd(
  state: EditorState,
  draft: NewTextDraft,
  { keep, returnFocus }: { keep: boolean; returnFocus: boolean },
): { typed: HistoryAction; ended: Partial<EditorState> } {
  const nothing: HistoryAction = { kind: "add", edits: [] };
  const back = {
    draft: null,
    ...(returnFocus && { focusTo: draft.of === null ? draft.back : { newTextId: draft.of.newTextId } }),
  };
  const edit = insertOf(draft);
  const unchanged = draft.of !== null && sameInsert(draft.of.edit, edit);
  if (!keep || unchanged || (draft.of === null && isEmpty(draft.text))) return { typed: nothing, ended: back };
  if (draft.of === null) {
    const added = {
      ...(returnFocus && { focusTo: { newTextId: state.history.next } }),
      announcement: announcing(fill(state.doc.sentences.new_text_added, { text: draft.text })),
    };
    return { typed: { kind: "add", edits: [edit] }, ended: { ...back, ...added } };
  }
  const typed: HistoryAction = { kind: "revise", replaces: draft.of.id, edit, folds: false };
  // Its words all taken out, it's gone, and its mark with it.
  if (isEmpty(draft.text)) {
    const out = {
      ...(returnFocus && { focusTo: draft.back }),
      announcement: announcing(fill(state.doc.sentences.new_text_taken_out, { text: draft.of.edit.text })),
    };
    return { typed, ended: { ...back, ...out } };
  }
  const changed = announcing(fill(state.doc.sentences.changed_to, { text: draft.text }));
  return { typed, ended: { ...back, announcement: changed } };
}

/**
 * End the typing: `keep` puts what was typed in the history. `returnFocus` sends focus back to
 * the span, as Enter and Escape do; leaving the field any other way has put it somewhere already.
 * `also` is the rest of the action that ends it, made in the same update.
 */
export function finish(
  editor: Editor,
  keep: boolean,
  { returnFocus = false, also = {} }: { returnFocus?: boolean; also?: Also } = {},
): void {
  const { typed, ended } = typingEnd(editor.store.get(), keep, { returnFocus });
  change(editor, typed, (next) => ({ ...ended, ...(typeof also === "function" ? also(next) : also) }));
}
