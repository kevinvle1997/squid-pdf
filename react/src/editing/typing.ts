// Typing in place: a draft, held apart from the history until it ends. It ends once, however
// it ends: Enter, Escape, leaving the field, or an export taking it along.
import { type Editor, change } from "./editor";

/** Start typing into a span, from what it reads now. */
export function edit(editor: Editor, spanId: string, atPt: number | null): void {
  const { store } = editor;
  const { layout, view } = store.get();
  const span = layout.spans.get(spanId);
  if (span === undefined) return;
  store.set({ draft: { spanId, page: span.page, atPt, text: view.spans.get(spanId)?.text ?? span.text } });
}

export function type(editor: Editor, text: string): void {
  const { store } = editor;
  const { draft } = store.get();
  if (draft !== null) store.set({ draft: { ...draft, text } });
}

/** End the typing: `keep` puts what was typed in the history. */
export function finish(editor: Editor, keep: boolean): void {
  const { store } = editor;
  const { draft, layout, view } = store.get();
  if (draft === null) return;
  store.set({ draft: null });
  const was = view.spans.get(draft.spanId)?.text ?? layout.spans.get(draft.spanId)?.text;
  // Emptying a span isn't a replacement: taking text out is redaction's job.
  if (!keep || draft.text === was || draft.text.trim() === "") return;
  change(editor, { kind: "add", edits: [{ kind: "replace", span_id: draft.spanId, text: draft.text }] });
  store.set({ said: `Changed to ${draft.text}` });
}

/** Focus went back to a span after its edit, or moved on from it. */
export function returnTo(editor: Editor, spanId: string | null): void {
  editor.store.set({ returnedTo: spanId });
}
