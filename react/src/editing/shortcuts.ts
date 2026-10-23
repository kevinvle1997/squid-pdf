// The list of the editor's keys, open or shut: from its button in the bar, or ? anywhere but a field.
import type { Editor } from "./editor";

/** Open the list of keys, or shut it. */
export function showShortcuts(editor: Editor, open: boolean): void {
  if (editor.store.get().shortcutsOpen !== open) editor.store.set({ shortcutsOpen: open });
}
