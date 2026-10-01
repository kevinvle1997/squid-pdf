// Typing in place: a draft, held apart from the history until it ends. It ends once, however
// it ends: Enter, Escape, leaving the field, or an export taking it along.
import { change, type Editor, type EditorState } from "./editor";
import { previewFaceOf } from "./faces";
import { fitOf, troubleKindOf, troublesOf } from "./fit";

/** Start typing into a span, from what it reads now. */
export function edit(editor: Editor, spanId: string, atPt: number | null): void {
  const { store } = editor;
  const { layout, reading } = store.get();
  const span = layout.spans.get(spanId);
  if (span === undefined) return;
  const text = reading.spans.get(spanId)?.text ?? span.text;
  store.set({ draft: { spanId, page: span.page, atPt, text }, focusTo: null });
}

/** What the draft's span would get wrong reading `text`, in the server's words; nothing when it fits. */
export function troublesIn(state: EditorState, text: string): { said: string[]; kind: string } {
  const span = state.draft === null ? undefined : state.layout.spans.get(state.draft.spanId);
  const font = span === undefined ? undefined : state.layout.fonts.get(span.font);
  if (span === undefined || font === undefined) return { said: [], kind: "" };
  const { fit: rules, copy } = state.doc;
  // A form field draws it, not the page: whatever is typed, the edit is left out.
  if (span.form_field) return { said: [copy.form_field_not_edited], kind: "form field" };
  const fit = fitOf(span, font, text, rules);
  const substitute = font.substitute ?? previewFaceOf(font);
  return { said: troublesOf(fit, rules, copy, substitute), kind: troubleKindOf(fit, rules) };
}

export function typeInto(editor: Editor, text: string): void {
  const { store } = editor;
  const state = store.get();
  if (state.draft === null) return;
  const was = troublesIn(state, state.draft.text);
  const now = troublesIn(state, text);
  // A trouble is said as it appears or changes, not as its numbers tick by with each letter.
  const said = now.kind !== was.kind && now.said.length > 0 ? now.said.join("; ") : state.said;
  store.set({ draft: { ...state.draft, text }, said });
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
  const was = reading.spans.get(draft.spanId)?.text ?? layout.spans.get(draft.spanId)?.text;
  // Emptying a span isn't a replacement: taking text out is redaction's job.
  if (!keep || draft.text === was || draft.text.trim() === "") {
    store.set(ended);
    return;
  }
  const replace = { kind: "replace" as const, span_id: draft.spanId, text: draft.text };
  change(editor, { kind: "add", edits: [replace] }, { ...ended, said: `Changed to ${draft.text}` });
}
