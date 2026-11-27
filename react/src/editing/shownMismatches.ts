import type { Editor } from "./editor";
import type { MismatchRow } from "./mismatches";

export function toggleShownMismatches(editor: Editor, row: MismatchRow): void {
  const shown = new Set(editor.store.get().shownMismatches);
  if (!shown.delete(row.key)) shown.add(row.key);
  editor.store.set({ shownMismatches: shown });
}
