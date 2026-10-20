// Where edits won't match, shown on the page from the list the bar's count opens: each row's eye.
import { type Editor, scrollingTo } from "./editor";
import type { UnlikeRow } from "./unlike";

/** Show `row`'s places on the page, bringing its first into view; shown already, stop. */
export function toggleShown(editor: Editor, row: UnlikeRow): void {
  const shown = new Set(editor.store.get().shownUnlike);
  // Shown already: pressed again, it stops.
  if (shown.delete(row.key)) {
    editor.store.set({ shownUnlike: shown });
    return;
  }
  shown.add(row.key);
  editor.store.set({ shownUnlike: shown, scrollTo: scrollingTo(row.first) });
}
