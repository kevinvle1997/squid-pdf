// Redaction: a span's words taken out of the file and checked by reading back what's saved (Rule 4); a
// keyboard command, not a tool (Rule 5). The server takes a whole span, so a selection inside one redacts it all.
import type { Edit } from "../api/types";
import { announcing, change, type Editor } from "./editor";
import { entriesOf } from "./history";
import { textIn } from "./project";
import { draftOf } from "./typing";
import { fill } from "./words";

/** Redact the span typed in, or else `spanId`: the whole of it, whatever part was selected. Words typed there go. */
export function redact(editor: Editor, spanId: string | null): void {
  const { draft, reading, layout, doc } = editor.store.get();
  const target = draft?.spanId ?? spanId;
  const span = target === null ? undefined : layout.spans.get(target);
  if (span === undefined || reading.spans.get(span.id)?.redacted) return;
  const redacted = fill(doc.sentences.redacted, { text: textIn(reading, span) });
  change(
    editor,
    { kind: "add", edits: [{ kind: "redact", span_id: span.id }] },
    // Typing into it ends unkept: redaction wins.
    { draft: null, announcement: announcing(redacted), focusTo: { spanId: span.id }, unredacting: null },
  );
}

/** "Edit anyway": the redaction leaves the history, then the span opens for typing as it reads without it. */
export function editAnyway(editor: Editor): void {
  const { unredacting, history, layout } = editor.store.get();
  const span = unredacting === null ? undefined : layout.spans.get(unredacting.spanId);
  if (unredacting === null || span === undefined) return;
  const isRedaction = (edit: Edit) => edit.kind === "redact" && edit.span_id === span.id;
  const redactions = new Set(entriesOf(history).flatMap((entry) => (isRedaction(entry.edit) ? [entry.id] : [])));
  change(editor, { kind: "remove", ids: redactions }, ({ reading }) => ({
    unredacting: null,
    draft: draftOf(span, { reading, press: unredacting.press }),
    focusTo: null,
  }));
}

/** "Keep redaction", or Escape: nothing changes, and focus goes back to the span. */
export function keepRedaction(editor: Editor): void {
  const { unredacting } = editor.store.get();
  if (unredacting === null) return;
  editor.store.set({ unredacting: null, focusTo: { spanId: unredacting.spanId } });
}
