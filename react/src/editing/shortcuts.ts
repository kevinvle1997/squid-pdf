import type { Editor } from "./editor";

export function showShortcuts(editor: Editor, open: boolean): void {
  if (editor.store.get().shortcutsOpen !== open) editor.store.set({ shortcutsOpen: open });
}
