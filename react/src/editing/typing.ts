// Typing in place: a draft, held apart from the history until it ends. It ends once, however
// it ends: Enter, Escape, leaving the field, or an export taking it along.
import type { FitRules, FontInfo, Insert, Replace, ReplaceRuns, SpanInfo, Strategy } from "../api/types";
import { type Press, selectionAt } from "./caret";
import { readDocumentDates } from "./documentDates";
import { type Also, announce, announcing, change, type Editor, type EditorState, type FocusTo } from "./editor";
import { type FieldHistory, NO_TYPING, recorded, redone, undone } from "./fieldHistory";
import {
  type Fit,
  fitOf,
  letterTroublesOf,
  lineFitOf,
  missingIn,
  runsPast,
  type TypedRun,
  troublesOf,
  wayDrawn,
  waysOffered,
} from "./fit";
import {
  editableValueIn,
  emailCountIn,
  type HeldValue,
  heldOf,
  heldThrough,
  heldValueIn,
  lineValueIn,
} from "./formattedValues";
import type { HistoryAction } from "./history";
import { type Line, runsRedrawn } from "./lines";
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
import { editVerdictOf, shortWarningOf, type Verdict, worstOf } from "./verdicts";
import { fill, sentenceOrNothing } from "./words";

/** The text being typed into a span, not yet in the history. */
export interface SpanDraft {
  readonly kind: "span";
  readonly spanId: string;
  readonly page: number;
  readonly press: Press | null; // the press that opened it, if one did: where the caret goes
  readonly caret: number | null; // where the caret goes as it opens, in the field's units, if typing put it there
  readonly text: string;
  readonly runs: readonly string[] | null; // a line's: each of its spans' words, in order, `text` read as one; else none
  readonly fieldHistory: FieldHistory; // a line field's own undo of its typing; an input keeps its own
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

/**
 * Start typing into a span, from what it reads now. A redacted span asks first, in place; one with some words redacted
 * opens, for more of its words to be selected, and asks as typing would change it.
 */
export function edit(editor: Editor, spanId: string, press: Press | null): void {
  const { store } = editor;
  const { layout, reading, doc } = store.get();
  const span = layout.spans.get(spanId);
  if (span === undefined) return;
  const line = layout.lines.get(spanId);
  // Un-redacting by accident is a leak: the one edit that asks first (quality.md, Modals and prompts).
  const redactedWhole = (line?.spans ?? [span]).some(({ id }) => reading.spans.get(id)?.redacted === "whole");
  if (redactedWhole) {
    const asking = { spanId: line?.id ?? spanId, press, typed: null };
    store.set({ unredacting: asking, announcement: announcing(doc.sentences.undo_redaction) });
    return;
  }
  readDocumentDates(editor);
  const draft = line === undefined ? draftOf(span, { reading, press }) : lineDraftOf(line, { reading, press });
  store.set({ draft, focusTo: null, unredacting: null });
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
    caret: null,
    text,
    runs: null,
    fieldHistory: NO_TYPING,
    strategy: now?.strategy,
    valueEditorOpen: false,
    value: null,
  };
}

/** Typing into `line` as its spans read in `reading`, opened by `press` from the line's start: one field, a run each. */
export function lineDraftOf(line: Line, { reading, press }: { reading: Reading; press: Press | null }): SpanDraft {
  const runs = line.spans.map((span) => textIn(reading, span));
  const page = line.spans[0]?.page ?? 0;
  const text = runs.join("");
  const unchosen = { caret: null, strategy: undefined, valueEditorOpen: false, value: null, fieldHistory: NO_TYPING };
  return { kind: "span", spanId: line.id, page, press, text, runs, ...unchosen };
}

/** What a line's runs come to reading `typed`, each with its font: none while one's font is unknown. */
function typedRunsIn(state: EditorState, { line, typed }: { line: Line; typed: readonly string[] }): TypedRun[] | null {
  const runs = line.spans.flatMap((span, index) => {
    const font = fontOf(state.layout.fonts, span);
    return font === undefined ? [] : [{ span, font, text: typed[index] ?? "" }];
  });
  return runs.length === line.spans.length ? runs : null;
}

/** The line the draft types into, with its runs as `typed`: none for a span alone, or a font unknown. */
function draftLineOf(state: EditorState, typed: readonly string[]): { line: Line; runs: TypedRun[] } | null {
  const draft = spanDraftOf(state);
  const line = draft === null ? undefined : state.layout.lines.get(draft.spanId);
  const runs = line === undefined ? null : typedRunsIn(state, { line, typed });
  return line === undefined || runs === null ? null : { line, runs };
}

/** Each run of a line typed as `runs` an edit redraws, with what it comes to, the way out `asked` taken while offered. */
function redrawnVerdictsOf(
  state: EditorState,
  { runs, fit, asked }: { runs: readonly TypedRun[]; fit: Fit; asked: Strategy | undefined },
): { run: TypedRun; verdict: Verdict }[] {
  const changed = runs.map(({ span, text }) => text !== span.text);
  const movesTheRest = wayDrawn(asked, fit) === "as-is" && fit.deltaPt !== 0;
  const redrawn = runsRedrawn(changed, { movesTheRest });
  return runs.flatMap((run, index) => {
    if (!redrawn[index]) return [];
    return [{ run, verdict: editVerdictOf(state.drawn, { ...run, strategy: asked }) }];
  });
}

/**
 * What the draft holds of the date or amount offered once it reads `text`, typed in: the one held, carried along
 * as the words before it change (`heldThrough`), else the one the press that opened the field landed on, else the
 * first (`editableValueIn`), and none while there's none. `lost`, when the one held is gone from the text: its kind
 * and where it lay are still held, so a value of that kind back over it is the one held again, as a year retyped
 * by hand passes through no date at all. Meanwhile, and if none ever comes back, the control offers what's there.
 */
function heldIn(
  state: EditorState & { draft: SpanDraft },
  text: string,
  runs: readonly string[] | null,
): { value: HeldValue | null; lost: boolean } {
  const { draft, layout, reading, dates } = state;
  const span = layout.spans.get(draft.spanId);
  if (span === undefined) return { value: null, lost: false };
  const held = draft.value === null ? null : heldThrough(draft.value, { before: draft.text, after: text });
  const glyphs = fontOf(layout.fonts, span)?.glyphs ?? {};
  const at = draft.press === null ? undefined : selectionAt(textIn(reading, span), draft.press, { glyphs, span })[0];
  // A line's value lies in one of its runs, and a press across it isn't measured here: the one held, else the first.
  const value =
    runs === null ? editableValueIn(text, { dates, at, held }) : lineValueIn(runs, { dates, at: undefined, held });
  const lost = held !== null && heldValueIn(text, { dates, held }) === undefined;
  if (lost) return { value: held, lost };
  return { value: value === undefined ? null : heldOf(value, text), lost };
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
  const { draft, span, font } = typing;
  const typedLine = draft.runs === null ? null : draftLineOf(state, draft.runs);
  if (typedLine !== null) return lineFitOf(typedLine.runs, { roomPt: typedLine.line.roomPt, rules: state.doc.fit });
  return fitOf(span, { font, text: draft.text, rules: state.doc.fit });
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

/** What the draft's span would get wrong reading `typed`, a line's as its runs, the ways out it offers, and what it comes to. */
export function troublesIn(state: EditorState, typed: string | readonly string[]): Troubles {
  if (typeof typed !== "string") return lineTroublesIn(state, typed);
  const text = typed;
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

/** What the draft's line would get wrong reading `typed`, a run each, and what its runs redrawn come to: the worst of them. */
function lineTroublesIn(state: EditorState, typed: readonly string[]): Troubles {
  const draft = spanDraftOf(state);
  const typedLine = draftLineOf(state, typed);
  if (draft === null || typedLine === null) return { sentences: [], kind: "", offers: false, verdict: null };
  const { line, runs } = typedLine;
  const { fit: rules, sentences } = state.doc;
  // Before a change, any run may be redrawn: each says so as the field opens.
  const unedited = { offers: false, verdict: worstOf(line.spans) };
  if (isEmpty(typed.join(""))) return { sentences: [sentences.empty], kind: "empty", ...unedited };
  const fit = lineFitOf(runs, { roomPt: line.roomPt, rules });
  const way = wayDrawn(draft.strategy, fit);
  const changed = runs.filter(({ span, text }) => text !== span.text);
  const drawings = changed.map(({ span, font, text }) =>
    drawingOf(span, { font, text, missing: missingIn(text, font.glyphs) }),
  );
  const letters = [...new Set(drawings.flatMap((drawing) => letterTroublesOf(drawing, sentences)))];
  // Too long is the whole line's, said once: its letters are each run's, above.
  const tooLong = troublesOf(fit, { rules, sentences, drawing: { face: "", switched: [], leftOut: [] }, way });
  const verdict = worstOf(redrawnVerdictsOf(state, { runs, fit, asked: draft.strategy }).map((each) => each.verdict));
  return {
    sentences: [...letters, ...tooLong],
    kind: `${troubleKindOf(fit, rules)} ${way} ${shortWarningOf(verdict) ?? ""} ${letters.join("")}`,
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

/**
 * Type into the field, so it reads `text`, the caret left at `caret`: what it warns of is heard as it changes. In a
 * span with some words redacted, it asks first, and what's typed waits for the answer.
 */
export function typeInto(editor: Editor, text: string, { caret = null }: { caret?: number | null } = {}): void {
  const state = editor.store.get();
  const { draft } = state;
  if (draft === null) return;
  if (draft.kind === "span") {
    // Typing in a span with words redacted would undo that: it asks first, the field shut, as a redacted one does.
    const undoesRedaction = state.reading.spans.get(draft.spanId)?.redacted != null && text !== draft.text;
    if (undoesRedaction) {
      const { sentences } = state.doc;
      const unredacting = { spanId: draft.spanId, press: null, typed: { text: asOneLine(text), runs: null, caret } };
      finish(editor, false, {
        // A reading persisted by an earlier build lacks the question: the whole line's asks it too.
        also: { unredacting, announcement: announcing(sentences.undo_redacted_words ?? sentences.undo_redaction) },
      });
      return;
    }
    editor.store.set(typedState({ ...state, draft }, text));
    return;
  }
  const line = asOneLine(text);
  editor.store.set({ draft: { ...draft, text: line }, announcement: heardAsTyped({ ...state, draft }, line) });
}

/** What a change to a line's field replaced: what was selected, and the words put there. */
export interface RunsChange {
  readonly selected: readonly [number, number];
  readonly typed: string;
}

/**
 * Type into a line's field, so its runs read `runs`, the caret left at `caret` across them: what it warns of is heard as
 * it changes, and the field's own undo keeps what it read before the change `made`. With any of its spans' words
 * redacted, it asks first, and what's typed waits for the answer.
 */
export function typeIntoRuns(
  editor: Editor,
  runs: readonly string[],
  { caret = null, made }: { caret?: number | null; made?: RunsChange } = {},
): void {
  const state = editor.store.get();
  const draft = spanDraftOf(state);
  const line = draft === null ? undefined : state.layout.lines.get(draft.spanId);
  if (draft === null || draft.runs === null || line === undefined) return;
  const changes = runs.some((run, index) => run !== draft.runs?.[index]);
  const undoesRedaction = changes && line.spans.some(({ id }) => state.reading.spans.get(id)?.redacted != null);
  if (undoesRedaction) {
    const { sentences } = state.doc;
    const typed = runs.map(asOneLine);
    const unredacting = { spanId: draft.spanId, press: null, typed: { text: typed.join(""), runs: typed, caret } };
    finish(editor, false, {
      also: { unredacting, announcement: announcing(sentences.undo_redacted_words ?? sentences.undo_redaction) },
    });
    return;
  }
  const next = typedRunsState({ ...state, draft }, runs);
  // Words left as they were are nothing to undo.
  if (!changes) {
    editor.store.set(next);
    return;
  }
  const at = caret ?? 0;
  const before = { runs: draft.runs, selected: made?.selected ?? ([at, at] as const) };
  const fieldHistory = recorded(draft.fieldHistory, { before, typed: made?.typed ?? "", caretAfter: at });
  editor.store.set({ ...next, draft: { ...next.draft, fieldHistory } });
}

/**
 * Cmd+Z or Cmd+Shift+Z in a line's field (`step`), with `selected` there: its words as typed before or after, and
 * what to select; null with nothing to step to.
 */
export function stepTyping(
  editor: Editor,
  step: "undo" | "redo",
  { selected }: { selected: readonly [number, number] },
): readonly [number, number] | null {
  const state = editor.store.get();
  const draft = spanDraftOf(state);
  if (draft?.runs == null) return null;
  const now = { runs: draft.runs, selected };
  const stepped = (step === "undo" ? undone : redone)(draft.fieldHistory, now);
  if (stepped === null) return null;
  const next = typedRunsState({ ...state, draft }, stepped.to.runs);
  editor.store.set({ ...next, draft: { ...next.draft, fieldHistory: stepped.history } });
  return stepped.to.selected;
}

/** What typing a line's draft to read `typed`, a run each, makes of the state: its runs, and a trouble heard as it appears. */
export function typedRunsState(
  state: EditorState & { draft: SpanDraft },
  typed: readonly string[],
): Pick<EditorState, "announcement"> & Partial<Pick<EditorState, "focusTo">> & { draft: SpanDraft } {
  const runs = typed.map(asOneLine);
  const was = troublesIn(state, state.draft.runs ?? []);
  const announcement = heardOfTroubles(state, { was, now: troublesIn(state, runs) });
  return { ...withValueHeld(state, { text: runs.join(""), runs }), announcement };
}

/**
 * The draft reading `text`, a line's as `runs`, with the value held carried along: its editor goes, and focus to the
 * field, once the value open there is gone from the text.
 */
function withValueHeld(
  state: EditorState & { draft: SpanDraft },
  { text, runs }: { text: string; runs: readonly string[] | null },
): Partial<Pick<EditorState, "focusTo">> & { draft: SpanDraft } {
  // The value whose editor is open gone from the text, the editor goes with it, and focus goes to the field: where it
  // is when typed there, and never to the page's body from the editor going under it. Never another value's editor in
  // its place, taking focus from the field typed in.
  const { value, lost } = heldIn(state, text, runs);
  const closing = state.draft.valueEditorOpen && lost;
  const valueEditorOpen = state.draft.valueEditorOpen && !lost;
  return {
    draft: { ...state.draft, text, runs, value, valueEditorOpen },
    ...(closing && { focusTo: { spanId: state.draft.spanId } }),
  };
}

/** What typing `typed` into a span's draft makes of the state: its text, and a trouble announced as it appears. */
export function typedState(
  state: EditorState & { draft: SpanDraft },
  typed: string,
): Pick<EditorState, "announcement"> & Partial<Pick<EditorState, "focusTo">> & { draft: SpanDraft } {
  const text = asOneLine(typed);
  const announcement = heardAsTyped(state, text);
  return { ...withValueHeld(state, { text, runs: null }), announcement };
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
  const text = asOneLine(typed);
  const widthPt = widthOf(insertOf({ ...draft, text }));
  const origin = keptOnPage(info, { origin: draft.origin, size: draft.style.size }, widthPt);
  editor.store.set({ draft: { ...draft, text, origin }, announcement: heardAsTyped({ ...state, draft }, text) });
}

/** What's typed as one line: a gap for each break, and nothing a line can't draw. */
export function asOneLine(typed: string): string {
  return typed.replace(SPACING_CONTROL, " ").replace(NOT_ON_A_LINE, "");
}

/** What typing `text` makes heard: a trouble as it appears or changes, the chip's short warning as it changes kind. */
function heardAsTyped(state: EditorState & { draft: Draft }, text: string): EditorState["announcement"] {
  return heardOfTroubles(state, { was: troublesIn(state, state.draft.text), now: troublesIn(state, text) });
}

/** What a change from troubles `was` to `now` makes heard: what's new of them, never per letter. */
function heardOfTroubles(
  state: EditorState,
  { was, now }: { was: Troubles; now: Troubles },
): EditorState["announcement"] {
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
  if (draft?.runs != null) return lineNoteOf(state, draft.runs).note;
  const span = draft === null ? undefined : layout.spans.get(draft.spanId);
  if (span === undefined) return null;
  const muted = mutedFonts.has(shownNameIn(layout.fonts, span));
  return editNote(span, { verdict, font: fontOf(layout.fonts, span), sentences: doc.sentences, muted });
}

/**
 * What a line's draft reading `typed` warns of in full, in the chip: the note of each run it redraws that won't match,
 * each font's once, and those fonts by the names shown, which "Don't show again" mutes. Nothing once they match.
 */
export function lineNoteOf(state: EditorState, typed: readonly string[]): { note: Note | null; fonts: string[] } {
  const draft = spanDraftOf(state);
  const typedLine = draftLineOf(state, typed);
  if (draft === null || typedLine === null) return { note: null, fonts: [] };
  const { line, runs } = typedLine;
  const { layout, doc, mutedFonts } = state;
  const fit = lineFitOf(runs, { roomPt: line.roomPt, rules: doc.fit });
  const notes = new Map<string, Note>();
  for (const { run, verdict } of redrawnVerdictsOf(state, { runs, fit, asked: draft.strategy })) {
    const shown = shownNameIn(layout.fonts, run.span);
    const muted = mutedFonts.has(shown);
    const note = editNote(run.span, { verdict, font: run.font, sentences: doc.sentences, muted });
    if (note !== null && !notes.has(shown)) notes.set(shown, note);
  }
  const said = [...notes.values()];
  if (said.length === 0) return { note: null, fonts: [] };
  const joined = (parts: (string | null)[]) => [...new Set(parts.filter((part) => part !== null))].join(" ");
  const why = joined(said.map((note) => note.why));
  return {
    note: { warn: true, sentence: joined(said.map((note) => note.sentence)), why: why === "" ? null : why },
    fonts: [...notes.keys()],
  };
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
  const { sentences, verdict } = troublesIn(
    state,
    draft.kind === "span" && draft.runs !== null ? draft.runs : draft.text,
  );
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

/** The edit a line's typing, its runs `typed`, is kept as: every run's words, since the server fits the line as one. */
function keptLineEditOf(state: EditorState, typed: readonly string[]): ReplaceRuns | null {
  const draft = spanDraftOf(state);
  const line = draft === null ? undefined : state.layout.lines.get(draft.spanId);
  if (draft === null || line === undefined) return null;
  const way = wayIn(state);
  const strategy = way === "as-is" ? undefined : way;
  const before = line.spans.map((span) => state.reading.spans.get(span.id));
  const sameWords = line.spans.every((span, index) => typed[index] === textIn(state.reading, span));
  const sameWay = strategy === before.find((reading) => reading?.lineEdit !== undefined)?.strategy;
  // Emptying a line isn't a replacement: taking text out is redaction's job.
  if ((sameWords && sameWay) || isEmpty(typed.join(""))) return null;
  const runs = line.spans.map((span, index) => ({ span_id: span.id, text: typed[index] ?? "" }));
  return { kind: "replace_runs", runs, ...(strategy && { strategy }) };
}

/** Whether typed text says nothing: no change, since taking text out is redaction's job. */
export function isEmpty(text: string): boolean {
  return text.trim() === "";
}

/** The edit a span's or a line's typing is kept as once it ends: none while neither is typed in, or nothing would change. */
export function keptEditOf(state: EditorState): Replace | ReplaceRuns | null {
  const { layout, reading } = state;
  const draft = spanDraftOf(state);
  if (draft === null) return null;
  if (draft.runs !== null) return keptLineEditOf(state, draft.runs);
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
 * edit unless `keep` and the words changed, and the rest it sets. `returnFocus` and `inPlace` are `finish`'s.
 */
export function typingEnd(
  state: EditorState,
  keep: boolean,
  { returnFocus = false, inPlace = false } = {},
): { typed: HistoryAction; ended: Partial<EditorState> } {
  const { draft, doc } = state;
  const nothing: HistoryAction = { kind: "add", edits: [] };
  if (draft === null) return { typed: nothing, ended: {} };
  if (draft.kind === "newText") return newTextTypingEnd(state, draft, { keep, returnFocus, inPlace });
  const ended = { draft: null, ...(returnFocus && { focusTo: sentBack({ spanId: draft.spanId }, inPlace) }) };
  const replace = keptEditOf(state);
  if (!keep || replace === null) return { typed: nothing, ended };
  return {
    typed: { kind: "add", edits: [replace] },
    ended: { ...ended, announcement: announcing(fill(doc.sentences.changed_to, { text: draft.text })) },
  };
}

/** Focus sent back to `target` as the typing ends: a mark, or a page's control, takes it where the view is, `inPlace`. */
function sentBack(target: FocusTo | null, inPlace: boolean): FocusTo | null {
  const takesItInPlace = target !== null && ("spanId" in target || "newTextId" in target || "page" in target);
  if (!inPlace || !takesItInPlace) return target;
  return { ...target, inPlace };
}

/** What ending new text's typing makes: the text, its next version, nothing, or, emptied, its taking out. */
function newTextTypingEnd(
  state: EditorState,
  draft: NewTextDraft,
  { keep, returnFocus, inPlace }: { keep: boolean; returnFocus: boolean; inPlace: boolean },
): { typed: HistoryAction; ended: Partial<EditorState> } {
  const nothing: HistoryAction = { kind: "add", edits: [] };
  const back = {
    draft: null,
    ...(returnFocus && {
      focusTo: sentBack(draft.of === null ? draft.back : { newTextId: draft.of.newTextId }, inPlace),
    }),
  };
  const edit = insertOf(draft);
  const unchanged = draft.of !== null && sameInsert(draft.of.edit, edit);
  if (!keep || unchanged || (draft.of === null && isEmpty(draft.text))) return { typed: nothing, ended: back };
  if (draft.of === null) {
    const added = {
      ...(returnFocus && { focusTo: sentBack({ newTextId: state.history.next }, inPlace) }),
      announcement: announcing(fill(state.doc.sentences.new_text_added, { text: draft.text })),
    };
    return { typed: { kind: "add", edits: [edit] }, ended: { ...back, ...added } };
  }
  const typed: HistoryAction = { kind: "revise", replaces: draft.of.id, edit, folds: false };
  // Its words all taken out, it's gone, and its mark with it.
  if (isEmpty(draft.text)) {
    const out = {
      ...(returnFocus && { focusTo: sentBack(draft.back, inPlace) }),
      announcement: announcing(fill(state.doc.sentences.new_text_taken_out, { text: draft.of.edit.text })),
    };
    return { typed, ended: { ...back, ...out } };
  }
  const changed = announcing(fill(state.doc.sentences.changed_to, { text: draft.text }));
  return { typed, ended: { ...back, announcement: changed } };
}

/**
 * End the typing: `keep` puts what was typed in the history. `returnFocus` sends focus back to
 * the span, as Enter and Escape do; leaving the field for somewhere has put it there already.
 * `inPlace`, it's taken where the view is. `also` is the rest of the action that ends it, made in the same update.
 */
export function finish(
  editor: Editor,
  keep: boolean,
  { returnFocus = false, inPlace = false, also = {} }: { returnFocus?: boolean; inPlace?: boolean; also?: Also } = {},
): void {
  const { typed, ended } = typingEnd(editor.store.get(), keep, { returnFocus, inPlace });
  change(editor, typed, (next) => ({ ...ended, ...(typeof also === "function" ? also(next) : also) }));
}
