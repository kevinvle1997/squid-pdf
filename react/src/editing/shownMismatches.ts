// Where edits won't match, shown on the page from the list the bar's count opens: each row's eye.
import type { Editor } from "./editor";
import type { MismatchRow } from "./mismatches";

/** Show `row`'s places on the page where they are, or stop showing them: the page never moves. */
export function toggleShownMismatches(editor: Editor, row: MismatchRow): void {
  const shown = new Set(editor.store.get().shownMismatches);
  if (!shown.delete(row.key)) shown.add(row.key);
  editor.store.set({ shownMismatches: shown });
}
